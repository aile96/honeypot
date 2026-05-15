FROM mirror.gcr.io/free5gc/pcf:v4.2.2

# The Helm chart starts PCF through /usr/local/bin/pcf-wrapper so the lab can run
# a controlled pre-start hook before delegating to the original ./pcf binary.
COPY prestart.sh /usr/local/bin/prestart.sh
COPY pcf-wrapper.sh /usr/local/bin/pcf-wrapper
RUN chmod +x /usr/local/bin/prestart.sh /usr/local/bin/pcf-wrapper
