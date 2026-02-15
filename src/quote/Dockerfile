# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM php:8.3-cli@sha256:0ce2b71a35767f95c6e7b02550d0bd2668215a35cd04a83773a540f5c2b610da AS base
ARG QUOTE_PORT=8080

ADD https://github.com/mlocati/docker-php-extension-installer/releases/download/2.9.30/install-php-extensions /usr/local/bin/
RUN echo "68aa2cbd4740bf6db4f9ae005f90cf5892719dbeb1a01847dd068e8fe8994bd3  /usr/local/bin/install-php-extensions" | sha256sum -c - \
  && chmod +x /usr/local/bin/install-php-extensions \
  && install-php-extensions \
    opcache \
    pcntl \
    protobuf \
    opentelemetry

WORKDIR /var/www
CMD ["php", "public/index.php"]
USER www-data
EXPOSE ${QUOTE_PORT}

FROM composer:2.7@sha256:cb3483dc851665462a66c59982577dfbbde0ae2059e8b5550c2f49f44b8c333e AS vendor

WORKDIR /tmp/
COPY ./src/quote/composer.json .
COPY ./src/quote/composer.lock .

RUN composer install \
    --ignore-platform-reqs \
    --no-interaction \
    --no-plugins \
    --no-scripts \
    --no-dev \
    --prefer-dist

FROM base AS final
ARG QUOTE_PORT=8080
ENV QUOTE_PORT=${QUOTE_PORT}
COPY --from=vendor /tmp/vendor/ ./vendor/
COPY ./src/quote/ /var/www
