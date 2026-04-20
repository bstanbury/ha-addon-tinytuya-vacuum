ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.11-alpine3.18
FROM ${BUILD_FROM}

RUN pip3 install --no-cache-dir tinytuya flask requests

COPY run.sh /
COPY server.py /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]