

all : python-recs nanonet signalalign chiron nanotensor pypore python_utils

python-recs:
	pip install -U setuptools

marginAlign:
	pip install marginAlign
	pip install -U numpy
	pip install -U h5py

signalalign:
	cd signalAlign && make && python setup.py install

python_utils:
	echo "NOT PYPORE MAN"
	cd python_utils && python setup.py install

pypore:
	echo "PYPORE MAN"
	cd PyPore && python setup.py install

nanotensor:
	python setup.py install

chiron:
	cd chiron && python setup.py install

nanonet:
	cd nanonet && python setup.py install

clean:
	cd signalAlign && make clean

test:
	cd signalAlign && make test
	cd nanotensor && pytest
	cd python_utils && pytest

.PHONY: nanonet nanotensor signalalign python-recs marginAlign python_utils pypore chiron
