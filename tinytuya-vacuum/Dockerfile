ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install Python and pip
RUN apk add --no-cache python3 py3-pip

# Install dependencies
RUN pip3 install --no-cache-dir --break-system-packages tinytuya flask

# Copy files
COPY run.sh /
COPY server.py /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
