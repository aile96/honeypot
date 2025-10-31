# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM php:8.3-cli AS base
ARG QUOTE_PORT=8080

ADD https://github.com/mlocati/docker-php-extension-installer/releases/latest/download/install-php-extensions /usr/local/bin/
RUN chmod +x /usr/local/bin/install-php-extensions \
  && install-php-extensions \
    opcache \
    pcntl \
    protobuf \
    opentelemetry

WORKDIR /var/www
CMD ["php", "public/index.php"]
USER www-data
EXPOSE ${QUOTE_PORT}

FROM composer:2.7 AS vendor

WORKDIR /tmp/
COPY ./src/quote/composer.json .

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
