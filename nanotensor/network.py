#!/usr/bin/env python
"""Create BLSTM networks with various layers using TensorFlow"""
########################################################################
# File: networks.py
#  executable: network.py
# separate model class with a bulid model function
# keep objects as class variables
# and a separate class for running the model
# use json for hyperparameters


# Author: Andrew Bailey
# History: 5/20/17 Created
########################################################################

from __future__ import print_function
import sys
import os
from timeit import default_timer as timer
from datetime import datetime
from nanotensor.utils import project_folder, list_dir, load_json, save_json
from nanotensor.error import Usage
import tensorflow as tf
from tensorflow.contrib import rnn
import numpy as np
import logging as log


class BuildGraph(object):
    """Build a tensorflow network graph."""

    def __init__(self, x_iterator=None, y_iterator=None, seq_iterator=None, network=None, len_x=1, len_y=1,
                 learning_rate=0, seq_len=0, forget_bias=5.0, reuse=None, mode=0,
                 y_shape=None, x_shape=None, seq_shape=None, cost_function="cross_entropy", summary_name="Training"):

        assert (x_iterator is None) != (x_shape is None), "Either x_shape or x_iterator must be specified"
        if mode == 0 or mode == 1:
            assert (y_iterator is None) != (y_shape is None), "Either y_shape or y_iterator must be specified"
        assert (seq_iterator is None) != (seq_shape is None), "Either seq_shape or seq_iterator must be specified"
        assert network is not None, "Must specify network structure. [{'type': 'blstm', 'name': 'blstm_layer1', " \
                                    "'size': 128}, ...] "

        # initialize basic shape information
        self.x_iterator = x_iterator
        self.y_iterator = y_iterator
        self.seq_iterator = seq_iterator
        self.x_shape = x_shape
        self.y_shape = y_shape
        self.seq_shape = seq_shape
        self.len_x = len_x
        self.len_y = len_y
        self.seq_len = seq_len
        self.learning_rate = learning_rate
        self.forget_bias = forget_bias
        self.mode = mode
        self.summary_name = summary_name
        # TODO if retrain grab old global step
        self.global_step = tf.get_variable(
            'global_step', [],
            initializer=tf.constant_initializer(0), trainable=False)

        # Summary Information
        self.summaries = []

        # bool for multi-gpu usage
        self.reuse = reuse

        # initialize placeholders or take in tensors from a queue or Dataset object
        self.x = self.create_x()

        self.sequence_length = self.create_sequence_length()
        self.batch_size = tf.shape(self.x)[0]

        # length of input tensor, step number and number of classes for output layer

        # self.network = network
        self.output, final_layer_size = self.create_model(network_model=network)
        # create fully connected layer with correct number of classes
        with tf.name_scope("output_layer"):
            self.output_layer = self.create_output_layer()
            # tf.add_to_collection("output_layer", self.output_layer)
        log.info("Shape {}".format(self.output_layer.get_shape()))

        # create prediction
        with tf.name_scope("prediction_function"):
            self.prediction = self.prediction_function()
        if self.mode == 0 or self.mode == 1:
            self.y = self.create_y()
            # create accuracy function
            with tf.name_scope("accuracy_function"):
                self.accuracy = self.accuracy_function()
                self.variable_summaries(self.accuracy)
            # Define loss and cost function
            with tf.name_scope("cost_function"):
                self.cost = self.create_cost_function(cost_function)
                self.variable_summaries(self.cost)
            # Define optimizer
            if False:
                with tf.name_scope("optimizer_function"):
                    self.optimizer = self.optimizer_function()

            # merge summary information
            self.merged_summaries = tf.summary.merge(self.summaries)

    def create_x(self):
        return self.x_iterator

    def create_sequence_length(self):
        return self.seq_iterator

    def create_y(self):
        return self.y_iterator

    def create_output_layer(self):
        """Create standard output layer"""
        self.flat_outputs = tf.reshape(self.output, [-1, final_layer_size])
        output_layer, _, _ = self.fulconn_layer(self.flat_outputs, self.len_y)
        return output_layer

    def create_model(self, network_model=None):
        """Create a model from a list of dictionaries with "name", "type", and "size" keys"""
        assert network_model is not None, "Must specify network structure. [{'type': 'blstm', 'name': 'blstm_layer1', " \
                                          "'size': 128}, ...] "
        ref_types = {"tanh": tf.tanh, "relu": tf.nn.relu, "sigmoid": tf.sigmoid,
                     "softplus": tf.nn.softplus, "none": None}
        prevlayer_size = 0
        input_vector = self.x
        for layer in network_model:
            log.info("Shape {}".format(input_vector.get_shape()))
            inshape = input_vector.get_shape().as_list()
            if layer["type"] == "blstm":
                ratio = self.seq_len / inshape[1]

                input_vector = self.blstm(input_vector=input_vector, n_hidden=layer["size"],
                                          layer_name=layer["name"], forget_bias=layer["bias"], reuse=self.reuse,
                                          concat=layer["concat"], sequence_length=self.sequence_length / ratio)
                prevlayer_size = layer["size"] * 2
            elif layer["type"] == "lstm":
                ratio = self.seq_len / inshape[1]
                input_vector = self.lstm(input_vector=input_vector, n_hidden=layer["size"],
                                         layer_name=layer["name"], forget_bias=layer["bias"],
                                         sequence_length=self.sequence_length / ratio)
                prevlayer_size = layer["size"]

            elif layer["type"] == "decoder_lstm":
                ratio = self.seq_len / inshape[1]
                input_vector = self.decoder_lstm(input_vector=input_vector, n_hidden=layer["size"],
                                                 layer_name=layer["name"], forget_bias=layer["bias"],
                                                 output_keep_prob=layer["output_keep_prob"])
                prevlayer_size = layer["size"]


            elif layer["type"] == "residual_layer":
                with tf.variable_scope(layer["name"]):
                    if len(inshape) != 4:
                        input_vector = tf.reshape(input_vector, [tf.shape(input_vector)[0], 1,
                                                                 inshape[1], inshape[2]])
                    input_vector = self.residual_layer(input_vector,
                                                       out_channel=layer["out_channel"],
                                                       i_bn=layer["batchnorm"])
                    inshape = input_vector.get_shape().as_list()
                    input_vector = tf.reshape(input_vector, [tf.shape(input_vector)[0], inshape[2],
                                                             inshape[3]], name='residual_features')
            elif layer["type"] == "chiron_fnn":
                with tf.variable_scope(layer["name"]):
                    ### split blstm output and put fully con layer on each
                    lasth_rs = tf.reshape(input_vector, [tf.shape(input_vector)[0], inshape[1], 2, inshape[2] / 2],
                                          name='lasth_rs')
                    weight_out = tf.get_variable(name="weights", shape=[2, inshape[2] / 2],
                                                 initializer=tf.truncated_normal_initializer(
                                                     stddev=np.sqrt(2.0 / (inshape[2]))))
                    biases_out = tf.get_variable(name="bias", shape=[inshape[2] / 2], initializer=tf.zeros_initializer)
                    input_vector = tf.nn.bias_add(tf.reduce_sum(tf.multiply(lasth_rs, weight_out), axis=2), biases_out,
                                                  name='lasth_bias_add')
                    prevlayer_size = inshape[2] / 2
            else:
                with tf.variable_scope(layer["name"]):
                    #TODO make sure this is working
                    # reshape matrix to fit into a single activation function
                    input_vector = tf.reshape(input_vector, [-1, self.n_steps])
                    input_vector = self.fulconn_layer(input_data=input_vector, output_dim=layer["size"],
                                                      seq_len=1, activation_func=ref_types[layer["type"]])[0]
                    # reshape matrix to correct shape from output of
                    input_vector = tf.reshape(input_vector, [-1, self.n_steps, layer["size"]])
                    prevlayer_size = layer["size"]

        return input_vector, prevlayer_size

    def create_cost_function(self, cost_function):
        """Create cost function depending on cost function options"""
        if cost_function == "cross_entropy":
            # standard cross entropy implementation
            y_flat = tf.reshape(self.y, [-1, self.len_y])
            loss1 = tf.nn.softmax_cross_entropy_with_logits(logits=self.output_layer,
                                                            labels=y_flat)
        elif cost_function == "binary_cross_entropy":
            # cross entropy with binary labels.
            y_flat = tf.reshape(self.y, [-1, self.len_y])
            y_label_indices = tf.argmax(y_flat, 1, name="y_label_indices")
            loss1 = tf.nn.sparse_softmax_cross_entropy_with_logits(logits=self.output_layer,
                                                                   labels=y_label_indices)
        else:
            raise Usage("Cost function in config file is not one of the options: "
                        "[ctc_loss, binary_cross_entropy, cross_entropy]")
        cost = tf.reduce_mean(loss1)
        return cost

    def prediction_function(self):
        """Compare predicions with label to calculate number correct or edit distance"""
        predict = tf.argmax(self.output_layer, 1)
        return predict

    def accuracy_function(self):
        """Create accuracy function to calculate accuracy of the prediction"""
        y_flat = tf.reshape(self.y, [-1, self.len_y])
        accuracy = tf.reduce_mean(tf.cast(tf.equal(tf.argmax(self.output_layer, 1), tf.argmax(y_flat, 1)), tf.float32))
        return accuracy

    def optimizer_function(self):
        """Create optimizer function"""
        # opt = tf.train.AdamOptimizer(learning_rate=self.learning_rate)
        # self.gradients = opt.compute_gradients(self.cost)
        # learning_rate = tf.train.exponential_decay(self.learning_rate, self.global_step,
        #                                            100000, 0.96, staircase=True)
        return tf.train.AdamOptimizer(learning_rate=self.learning_rate).minimize(self.cost,
                                                                                 global_step=self.global_step)

    def variable_summaries(self, var):
        """Attach a lot of summaries to a Tensor (for TensorBoard visualization).
        source: https://github.com/tensorflow/tensorflow/blob/r1.1/tensorflow/examples/tutorials/mnist/mnist_with_summaries.py
        """
        with tf.name_scope(self.summary_name):
            with tf.name_scope('summaries'):
                mean = tf.reduce_mean(var)
                summary = tf.summary.scalar('mean', mean)
                self.summaries.append(summary)
                # with tf.name_scope("training"):
                #     with tf.name_scope('summaries'):
                #         mean = tf.reduce_mean(var)
                #         summary = tf.summary.scalar('mean', mean)
                #         self.training_summaries.append(summary)
                # with tf.name_scope('stddev'):
                #     stddev = tf.sqrt(tf.reduce_mean(tf.square(var - mean)))
                # tf.summary.scalar('stddev', stddev)
                # tf.summary.scalar('max', tf.reduce_max(var))
                # tf.summary.scalar('min', tf.reduce_min(var))

    @staticmethod
    def combine_arguments(tf_tensor_list, name):
        """Create a single operation that can be passed to the run function"""
        return tf.tuple(tf_tensor_list, name=name)

    @staticmethod
    def get_state_update_op(state_variables, new_states):
        """Update the state with new values"""
        # Add an operation to update the train states with the last state tensors
        update_ops = []
        for state_variable, new_state in zip(state_variables, new_states):
            # Assign the new state to the state variables on this layer
            update_ops.extend([state_variable[0].assign(new_state[0]),
                               state_variable[1].assign(new_state[1])])
        # Return a tuple in order to combine all update_ops into a single operation.
        # The tuple's actual value should not be used.
        return update_ops

    @staticmethod
    def blstm(input_vector, sequence_length, layer_name="blstm_layer1", n_hidden=128, forget_bias=5.0,
              reuse=None, concat=True):
        """Create a bidirectional LSTM using code from the example at
        https://github.com/aymericdamien/TensorFlow-Examples/blob/master/examples/3_NeuralNetworks/bidirectional_rnn.py
        """
        with tf.variable_scope(layer_name):
            # Define lstm cells with tensorflow
            # Forward direction cell
            lstm_fw_cell = rnn.LSTMCell(n_hidden, forget_bias=forget_bias, state_is_tuple=True, reuse=reuse)
            # Backward direction cell
            lstm_bw_cell = rnn.LSTMCell(n_hidden, forget_bias=forget_bias, state_is_tuple=True, reuse=reuse)

            outputs, output_states = tf.nn.bidirectional_dynamic_rnn(lstm_fw_cell,
                                                                     lstm_bw_cell, input_vector,
                                                                     dtype=tf.float32,
                                                                     sequence_length=sequence_length)
            # concat two output layers so we can treat as single output layer
            if concat:
                output = tf.concat(outputs, 2)
            else:
                output = outputs
        return output

    @staticmethod
    def fulconn_layer(input_data, output_dim, seq_len=1, activation_func=None):
        """Create a fully connected layer.
        source:
        https://stackoverflow.com/questions/39808336/tensorflow-bidirectional-dynamic-rnn-none-values-error/40305673
        """
        # get input dimensions
        input_dim = int(input_data.get_shape()[1])
        weight = tf.get_variable(name="weights", shape=[input_dim, output_dim * seq_len],
                                 initializer=tf.random_normal_initializer(stddev=np.sqrt(2.0 / (2 * output_dim))))
        bias = tf.get_variable(name="bias", shape=[output_dim * seq_len],
                               initializer=tf.zeros_initializer)

        # weight = tf.Variable(tf.random_normal([input_dim, output_dim * seq_len]), name="weights")
        # bias = tf.Variable(tf.random_normal([output_dim * seq_len]), name="bias")
        if activation_func:
            output = activation_func(tf.nn.bias_add(tf.matmul(input_data, weight), bias))
        else:
            output = tf.nn.bias_add(tf.matmul(input_data, weight), bias)
        return output, weight, bias

    ### The following funtions were originally taken from chiron https://github.com/haotianteng/Chiron
    def conv_layer(self, indata, ksize, padding, name, dilate=1, strides=[1, 1, 1, 1], bias_term=False, active=True,
                   BN=True):
        """A standard convlotional layer"""
        with tf.variable_scope(name):
            W = tf.get_variable("weights", dtype=tf.float32, shape=ksize,
                                initializer=tf.contrib.layers.xavier_initializer())
            if bias_term:
                b = tf.get_variable("bias", dtype=tf.float32, shape=[ksize[-1]])
            if dilate > 1:
                if bias_term:
                    conv_out = b + tf.nn.atrous_conv2d(indata, W, rate=dilate, padding=padding, name=name)
                else:
                    conv_out = tf.nn.atrous_conv2d(indata, W, rate=dilate, padding=padding, name=name)
            else:
                if bias_term:
                    conv_out = b + tf.nn.conv2d(indata, W, strides=strides, padding=padding, name=name)
                else:
                    conv_out = tf.nn.conv2d(indata, W, strides=strides, padding=padding, name=name)
        if BN:
            with tf.variable_scope(name + '_bn') as scope:
                #            conv_out = batchnorm(conv_out,scope=scope,training = training)
                conv_out = self.simple_global_bn(conv_out, name=name + '_bn')
        if active:
            with tf.variable_scope(name + '_relu'):
                conv_out = tf.nn.relu(conv_out, name='relu')
        return conv_out

    @staticmethod
    def simple_global_bn(inp, name):
        ksize = inp.get_shape().as_list()
        ksize = [ksize[-1]]
        mean, variance = tf.nn.moments(inp, [0, 1, 2], name=name + '_moments')
        scale = tf.get_variable(name + "_scale",
                                shape=ksize, initializer=tf.contrib.layers.variance_scaling_initializer())
        offset = tf.get_variable(name + "_offset",
                                 shape=ksize, initializer=tf.contrib.layers.variance_scaling_initializer())
        return tf.nn.batch_normalization(inp, mean=mean, variance=variance, scale=scale, offset=offset,
                                         variance_epsilon=1e-5)

    def inception_layer(self, indata, times=16):
        """Inception module with dilate conv layer from http://arxiv.org/abs/1512.00567"""
        fea_shape = indata.get_shape().as_list()
        in_channel = fea_shape[-1]
        with tf.variable_scope('branch1_AvgPooling'):
            avg_pool = tf.nn.avg_pool(indata, ksize=(1, 1, 3, 1), strides=(1, 1, 1, 1), padding='SAME',
                                      name='avg_pool0a1x3')
            conv1a = self.conv_layer(avg_pool, ksize=[1, 1, in_channel, times * 3], padding='SAME',
                                     name='conv1a_1x1')
        with tf.variable_scope('branch2_1x1'):
            conv0b = self.conv_layer(indata, ksize=[1, 1, in_channel, times * 3], padding='SAME',
                                     name='conv0b_1x1')
        with tf.variable_scope('branch3_1x3'):
            conv0c = self.conv_layer(indata, ksize=[1, 1, in_channel, times * 2], padding='SAME',
                                     name='conv0c_1x1')
            conv1c = self.conv_layer(conv0c, ksize=[1, 3, times * 2, times * 3], padding='SAME',
                                     name='conv1c_1x3')
        with tf.variable_scope('branch4_1x5'):
            conv0d = self.conv_layer(indata, ksize=[1, 1, in_channel, times * 2], padding='SAME',
                                     name='conv0d_1x1')
            conv1d = self.conv_layer(conv0d, ksize=[1, 5, times * 2, times * 3], padding='SAME',
                                     name='conv1d_1x5')
        with tf.variable_scope('branch5_1x3_dilate_2'):
            conv0e = self.conv_layer(indata, ksize=[1, 1, in_channel, times * 2], padding='SAME',
                                     name='conv0e_1x1')
            conv1e = self.conv_layer(conv0e, ksize=[1, 3, times * 2, times * 3], padding='SAME',
                                     name='conv1e_1x3_d2', dilate=2)
        with tf.variable_scope('branch6_1x3_dilate_3'):
            conv0f = self.conv_layer(indata, ksize=[1, 1, in_channel, times * 2], padding='SAME',
                                     name='conv0f_1x1')
            conv1f = self.conv_layer(conv0f, ksize=[1, 3, times * 2, times * 3], padding='SAME',
                                     name='conv1f_1x3_d3', dilate=3)
        return (tf.concat([conv1a, conv0b, conv1c, conv1d, conv1e, conv1f], axis=-1, name='concat'))

    def residual_layer(self, indata, out_channel, i_bn=False):
        fea_shape = indata.get_shape().as_list()
        in_channel = fea_shape[-1]
        with tf.variable_scope('branch1'):
            indata_cp = self.conv_layer(indata, ksize=[1, 1, in_channel, out_channel], padding='SAME',
                                        name='conv1', BN=i_bn, active=False)
        with tf.variable_scope('branch2'):
            conv_out1 = self.conv_layer(indata, ksize=[1, 1, in_channel, out_channel], padding='SAME',
                                        name='conv2a', bias_term=False)
            conv_out2 = self.conv_layer(conv_out1, ksize=[1, 3, out_channel, out_channel], padding='SAME',
                                        name='conv2b', bias_term=False)
            conv_out3 = self.conv_layer(conv_out2, ksize=[1, 1, out_channel, out_channel], padding='SAME',
                                        name='conv2c', bias_term=False, active=False)
        with tf.variable_scope('plus'):
            relu_out = tf.nn.relu(indata_cp + conv_out3, name='final_relu')
        return relu_out

    @staticmethod
    def lstm(input_vector, sequence_length, n_hidden, forget_bias, layer_name):
        """Define basic dynamic LSTM layer"""
        with tf.variable_scope(layer_name):
            rnn_layers = tf.nn.rnn_cell.LSTMCell(n_hidden, forget_bias=forget_bias, state_is_tuple=True)
            # 'outputs' is a tensor of shape [batch_size, max_time, 256]
            # 'state' is a N-tuple where N is the number of LSTMCells containing a
            # tf.contrib.rnn.LSTMStateTuple for each cell
            output, _ = tf.nn.dynamic_rnn(cell=rnn_layers,
                                          inputs=input_vector,
                                          dtype=tf.float32,
                                          time_major=False,
                                          sequence_length=sequence_length)
        return output

    def decoder_lstm(self, input_vector, n_hidden, forget_bias, layer_name, output_keep_prob=1):
        """Feeds output from lstm into input of same lstm cell"""

        with tf.variable_scope(layer_name):
            def make_cell(input_vector, state, mode):
                cell = tf.nn.rnn_cell.LSTMCell(n_hidden, forget_bias=forget_bias)
                if mode == 0 and output_keep_prob < 1:
                    cell = tf.contrib.rnn.DropoutWrapper(
                        cell, output_keep_prob=output_keep_prob)
                return cell(inputs=input_vector, state=state)

            # Simplified version of tensorflow_models/tutorials/rnn/rnn.py's rnn().
            # This builds an unrolled LSTM for tutorial purposes only.
            # In general, use the rnn() or state_saving_rnn() from rnn.py.
            #
            # The alternative version of the code below is:
            #
            # inputs = tf.unstack(inputs, num=num_steps, axis=1)
            # outputs, state = tf.contrib.rnn.static_rnn(cell, inputs,
            #                            initial_state=self._initial_state)
            state = tf.nn.rnn_cell.LSTMCell(n_hidden, forget_bias=forget_bias).zero_state(self.batch_size, tf.float32)

            outputs = []
            with tf.variable_scope("decoder_lstm"):
                for time_step in range(self.seq_len):
                    if time_step > 0:
                        tf.get_variable_scope().reuse_variables()
                    log.info("Shape {}".format(input_vector.get_shape()))
                    (cell_output, state) = make_cell(input_vector, state, mode=self.mode)
                    outputs.append(cell_output)
                    input_vector = cell_output
            output = tf.stack(outputs, 1)
        return output


class CrossEntropy(BuildGraph):
    """Build a tensorflow network graph."""

    def __init__(self, x_iterator=None, y_iterator=None, seq_iterator=None, network=None, learning_rate=0, seq_len=0,
                 forget_bias=5.0, reuse=None, y_shape=None, x_shape=None,
                 seq_shape=None, len_x=1, len_y=1, mode=0, dataset=None, summary_name="Training"):

        if dataset:
            len_x = dataset.len_x
            len_y = dataset.len_y
            seq_len = dataset.seq_len
            mode = dataset.mode
            if mode == 0 or mode == 1:
                x_iterator, seq_iterator, y_iterator = dataset.iterator.get_next()
            elif mode == 2:
                x_iterator, seq_iterator = dataset.iterator.get_next()

        super(CrossEntropy, self).__init__(x_iterator=x_iterator, y_iterator=y_iterator, seq_iterator=seq_iterator,
                                           network=network, learning_rate=learning_rate, seq_len=seq_len,
                                           forget_bias=forget_bias, reuse=reuse, mode=mode, y_shape=y_shape,
                                           x_shape=x_shape, seq_shape=seq_shape, len_x=len_x, len_y=len_y,
                                           summary_name=summary_name)


class CtcLoss(BuildGraph):
    """Build a tensorflow network graph."""

    def __init__(self, dataset=None, x_iterator=None, y_iterator=None, seq_iterator=None, network=None, learning_rate=0,
                 seq_len=0, forget_bias=5.0, reuse=None, training=True,
                 y_shape=None, x_shape=None, seq_shape=None, len_x=1, len_y=1, summary_name="Training"):

        # initialize placeholders or take in tensors from a queue or Dataset object
        if dataset:
            len_x = dataset.len_x
            len_y = dataset.len_y
            seq_len = dataset.seq_len
            mode = dataset.mode
            if mode == 0 or mode == 1:
                x_iterator, seq_iterator, y_iterator = dataset.iterator.get_next()
            elif mode == 2:
                x_iterator, seq_iterator = dataset.iterator.get_next()

        self.sparse_predict = "sparse_prediction"
        len_y = len_y + 1
        if y_shape:
            self.y_indexs = tf.placeholder(tf.int64)
            self.y_values = tf.placeholder(tf.int32)
            self.y_shape = tf.placeholder(tf.int64)

        super(CtcLoss, self).__init__(x_iterator=x_iterator, y_iterator=y_iterator, seq_iterator=seq_iterator,
                                      network=network, learning_rate=learning_rate, seq_len=seq_len,
                                      forget_bias=forget_bias, reuse=reuse, mode=mode, y_shape=y_shape,
                                      x_shape=x_shape, seq_shape=seq_shape, len_x=len_x, len_y=len_y,
                                      summary_name=summary_name)

    def create_output_layer(self):
        """Create output layer for ctc loss"""
        self.flat_outputs = tf.reshape(self.output, [-1, final_layer_size])
        output_layer, _, _ = self.fulconn_layer(self.flat_outputs, self.len_y)
        self.output_shape = self.output.get_shape().as_list()
        output_layer = tf.reshape(output_layer, shape=[-1, self.output_shape[1], self.len_y])
        return output_layer

    def create_x(self):
        """Replace main method to reshape the x input before feeding into network"""
        x = tf.placeholder_with_default(self.x_iterator, shape=self.x_shape)
        return tf.reshape(x, shape=[-1, self.seq_len, 1])

    def create_y(self):
        """Take in sparse vector or create sparse vector placeholder"""
        if self.y_iterator:
            y = tf.SparseTensor(self.y_iterator[0], self.y_iterator[1], self.y_iterator[2])
        else:
            y = tf.SparseTensor(self.y_indexs, self.y_values, self.y_shape)
        return y

    def prediction_function(self):
        """Compare predicions with label to calculate number correct or edit distance"""
        logits = tf.transpose(self.output_layer, perm=[1, 0, 2])
        self.sparse_predict = tf.nn.ctc_greedy_decoder(logits, self.sequence_length, merge_repeated=True)[0]
        predict = tf.sparse_tensor_to_dense(self.sparse_predict[0], default_value=-1)
        return predict

    def accuracy_function(self):
        """Create accuracy function to calculate accuracy of the prediction"""
        with tf.name_scope("edit_distance"):
            # get edit distance and return mean as accuracy
            edit_d = tf.edit_distance(tf.to_int32(self.sparse_predict[0]), self.y, normalize=True)
            accuracy = tf.reduce_mean(edit_d, axis=0)
        return accuracy

    def create_cost_function(self, cost_function):
        loss = tf.nn.ctc_loss(inputs=self.output_layer, labels=self.y,
                              sequence_length=self.sequence_length, ctc_merge_repeated=True,
                              time_major=False)
        cost = tf.reduce_mean(loss)
        return cost


def dense_to_sparse(dense_tensor):
    """Convert dense tensor to sparse tensor"""
    where_dense_non_zero = tf.where(tf.not_equal(dense_tensor, 0))
    indices = where_dense_non_zero
    values = tf.gather_nd(dense_tensor, where_dense_non_zero)
    shape = dense_tensor.get_shape()

    return tf.SparseTensor(
        indices=indices,
        values=values,
        dense_shape=shape)


def sparse_tensor_merge(indices, values, shape):
    """Creates a SparseTensor from batched indices, values, and shapes.

    Args:
      indices: A [batch_size, N, D] integer Tensor.
      values: A [batch_size, N] Tensor of any dtype.
      shape: A [batch_size, D] Integer Tensor.
    Returns:
      A SparseTensor of dimension D + 1 with batch_size as its first dimension.
    """
    # print(indices.get)
    merged_shape = tf.reduce_max(shape, axis=0)
    batch_size, elements, shape_dim = tf.unstack(tf.shape(indices))
    index_range_tiled = tf.tile(tf.range(batch_size)[..., None],
                                tf.stack([1, elements]))[..., None]
    merged_indices = tf.reshape(
        tf.concat([tf.cast(index_range_tiled, tf.int64), indices], axis=2),
        [-1, 1 + tf.size(merged_shape)])
    merged_values = tf.reshape(values, [-1])
    return tf.SparseTensor(
        merged_indices, merged_values,
        tf.concat([[tf.cast(batch_size, tf.int64)], merged_shape], axis=0))


if __name__ == "__main__":
    print("This file is just a library", file=sys.stderr)
    raise SystemExit
