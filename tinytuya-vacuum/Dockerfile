ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install Python dependencies
RUN pip3 install --no-cache-dir --break-system-packages tinytuya flask

# Copy application
COPY server.py /app/server.py
COPY run.sh /app/run.sh
RUN chmod a+x /app/run.sh

WORKDIR /app
ENTRYPOINT []
CMD ["/app/run.sh"]
