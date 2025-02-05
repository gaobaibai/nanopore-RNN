#!/usr/bin/env python
"""Plot information needed file"""
########################################################################
# File: plot_raw_read_alignment.py
#  executable: plot_raw_read_alignment.py
#
# Author: Andrew Bailey
# History: Created 12/01/17
########################################################################

from __future__ import print_function
import sys
import os
from timeit import default_timer as timer
import pysam
import matplotlib.pyplot as plt
import matplotlib.patches as mplpatches
import numpy as np
import scipy.stats as stats
import seaborn as sns
from py3helpers.utils import list_dir
from PyPore.parsers import SpeedyStatSplit
from nanonet.eventdetection.filters import minknow_event_detect
from nanotensor.fast5 import Fast5
from nanotensor.event_detection import resegment_reads, create_anchor_kmers, index_to_time_rna_basecall


def raw_scatter_plot(signal_data, label_data, outpath, interval):
    """plot accuracy distribution of reads"""
    # define figure size
    size = (interval[1] - interval[0]) / 100
    plt.figure(figsize=(size, 4))
    panel1 = plt.axes([0.01, 0.1, .95, .9])
    # longest = max(data[0]) + data[1])
    # panel1.set_xlim(0, 1000)
    mean = np.mean(signal_data)
    stdv = np.std(signal_data)
    panel1.set_ylim(mean - (3 * stdv), mean + (3 * stdv))
    panel1.set_xlim(interval[0], interval[1])

    # panel1.set_xscale("log")
    plt.scatter(x=range(len(signal_data)), y=signal_data, s=1, c="k")

    plt.title('Nanopore Read')
    for i in range(len(label_data.start)):
        if interval[0] < label_data.start[i] < interval[1]:
            panel1.text(label_data.start[i] + (label_data.length[i] / 2), 2, "{}".format(label_data.base[i]),
                        fontsize=10, va="bottom", ha="center")
            panel1.axvline(label_data.start[i])
            panel1.axvline(label_data.start[i] + label_data.length[i])
    plt.show()


    # plt.savefig(outpath)


def raw_scatter_plot_with_events(signal_data, label_data, outpath, interval, events):
    """plot accuracy distribution of reads"""
    # define figure size
    size = (interval[1] - interval[0]) / 75
    plt.figure(figsize=(size, 4))
    panel1 = plt.axes([0.01, 0.1, .95, .9])
    # longest = max(data[0]) + data[1])
    # panel1.set_xlim(0, 1000)
    mean = np.mean(signal_data)
    stdv = np.std(signal_data)
    panel1.set_ylim(mean - (3 * stdv), mean + (3 * stdv))
    panel1.set_xlim(interval[0], interval[1])

    # panel1.set_xscale("log")
    plt.scatter(x=range(len(signal_data)), y=signal_data, s=1, c="k")

    plt.title('Nanopore Read')
    for i in range(len(label_data.start)):
        if interval[0] < label_data.start[i] < interval[1]:
            panel1.text(label_data.start[i] + (label_data.length[i] / 2), 2, "{}".format(label_data.base[i]),
                        fontsize=10, va="bottom", ha="center")
            panel1.axvline(label_data.start[i])
            panel1.axvline(label_data.start[i] + label_data.length[i])

    for event_peak in events:
        if interval[0] < event_peak < interval[1]:
            panel1.axvline(event_peak, linestyle='--', color='r')

    plt.show()
    # plt.savefig(outpath)


def plot_raw_reads(current, old_events, resegment=None, dna=False, sampling_freq=4000, start_time=0, window_size=None):
    """Plot raw reads using ideas from Ryan Lorig-Roach's script"""
    fig1 = plt.figure(figsize=(24, 3))
    panel = fig1.add_subplot(111)
    prevMean = 0
    handles = list()
    handle, = panel.plot(current, color="black", lw=0.2)
    handles.append(handle)
    start = 0
    if window_size:
        start = old_events[0]["start"]
        end = old_events[-1]["start"]
        if dna:
            start = (start - (start_time / sampling_freq)) * sampling_freq
            end = (end - (start_time / sampling_freq)) * sampling_freq

        start = np.random.randint(start, end - window_size)

    # print(start, end - window_size)
    # print(len(old_events), len(resegment))
    for j, segment in enumerate(old_events):
        x0 = segment["start"]
        x1 = x0 + segment["length"]
        if dna:
            x0 = (x0 - (start_time / sampling_freq)) * sampling_freq
            x1 = (x1 - (start_time / sampling_freq)) * sampling_freq

        if start < x0 < (start + window_size):
            kmer = segment["model_state"]
            mean = segment['mean']
            color = [.082, 0.282, 0.776]
            handle1, = panel.plot([x0, x1], [mean, mean], color=color, lw=0.8)
            panel.plot([x0, x0], [prevMean, mean], color=color, lw=0.5)  # <-- uncomment for pretty square wave
            # panel.text(x0, mean - 2, bytes.decode(kmer), fontsize=5)
            prevMean = mean

    handles.append(handle1)
    panel.set_title("Signal")
    panel.set_xlabel("Time (ms)")
    panel.set_ylabel("Current (pA)")

    if resegment is not None:
        color = [1, 0.282, 0.176]
        prevMean = 0
        for indx, segment in enumerate(resegment):
            kmer = segment["model_state"]
            x0 = segment["raw_start"]
            x1 = x0 + segment["raw_length"]
            mean = segment['mean']
            if start < x0 < start + window_size:
                handle2, = panel.plot([x0, x1], [mean, mean], color=color, lw=0.8)
                panel.plot([x0, x0], [prevMean, mean], color=color, lw=0.5)  # <-- uncomment for pretty square wave
                panel.text(x0, mean + 2, bytes.decode(kmer), fontsize=5)
                prevMean = mean

        handles.append(handle2)

    box = panel.get_position()
    panel.set_position([box.x0, box.y0, box.width * 0.95, box.height])
    if len(handles) == 3:
        plt.legend(handles, ["Raw", "OriginalSegment", "New Segment"], loc='upper left', bbox_to_anchor=(1, 1))
    else:
        plt.legend(handles, ["Raw", "OriginalSegment"], loc='upper left', bbox_to_anchor=(1, 1))

    plt.show()


def plot_segmented_comparison(fast5_handle, window_size=None):
    """Plot read with segmented lines and kmers.

    :param fast5_handle: Fast5 instance where there is already a resegemented analysis table
    :param window_size: size of window to display instead of whole file
    """
    events = fast5_handle.get_basecall_data()
    signal = fast5_handle.get_read(raw=True, scale=True)
    resegment_events = fast5_handle.get_resegment_basecall()
    if fast5_handle.is_read_rna():
        plot_raw_reads(signal, events, resegment=resegment_events, window_size=window_size)
    else:
        start_time = fast5_handle.raw_attributes["start_time"]
        sampling_freq = fast5_handle.sample_rate
        plot_raw_reads(signal, events, resegment=None, dna=True, sampling_freq=sampling_freq,
                       start_time=start_time, window_size=window_size)


def main():
    """Main docstring"""
    start = timer()
    minknow_params = dict(window_lengths=(5, 10), thresholds=(2.0, 1.1), peak_height=1.2)
    speedy_params = dict(min_width=5, max_width=30, min_gain_per_sample=0.008, window_width=800)

    dna_reads = "/Users/andrewbailey/CLionProjects/nanopore-RNN/test_files/minion-reads/canonical/"
    files = list_dir(dna_reads, ext='fast5')
    rna_reads = "/Users/andrewbailey/CLionProjects/nanopore-RNN/test_files/minion-reads/rna_reads"
    # files = list_dir(rna_reads, ext='fast5')

    print(files[0])
    f5fh = Fast5(files[0])
    # f5fh = resegment_reads(files[0], minknow_params, speedy=False, overwrite=True)
    plot_segmented_comparison(f5fh, window_size=3000)

    stop = timer()
    print("Running Time = {} seconds".format(stop - start), file=sys.stderr)


if __name__ == "__main__":
    main()
    raise SystemExit
