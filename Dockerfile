FROM python:2.7-alpine3.7
RUN apk update && apk add libffi-dev openssl-dev

RUN apk add --no-cache \
    gcc \
    libffi-dev \
    musl-dev \
    openssl-dev \
    python3-dev \
    yaml-dev \
    && mkdir /app
COPY . /app
WORKDIR /app
# force dateutil otherwise installation fails
RUN pip install requests python-dateutil==2.6.1 && python setup.py develop
 
ENTRYPOINT /usr/local/bin/kubestash