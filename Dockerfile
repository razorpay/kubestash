FROM python:3.7-alpine3.16
# See https://github.com/pyca/cryptography/blob/master/docs/installation.rst#alpine
ENV PYTHONUNBUFFERED=0
RUN apk update \
 && apk upgrade --no-progress
RUN apk add --no-cache \
    gcc \
    libffi-dev \
    musl-dev \
    openssl-dev \
    python3-dev \
    yaml-dev \
    && mkdir /app

WORKDIR /app
# force dateutil otherwise installation fails
RUN pip install requests python-dateutil==2.6.1
COPY ./requirements.txt ./requirements.txt
RUN pip install -r requirements.txt
COPY . /app
RUN python setup.py install
