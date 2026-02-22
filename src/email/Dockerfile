# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM ruby:3.4.4-alpine3.21@sha256:5bdfa2312f3f0bc9bd077e954c943652155a093db228af978873ff92389300d3 AS builder

COPY Gemfile Gemfile
COPY Gemfile.lock Gemfile.lock

RUN apk update && \
    apk add make=4.4.1-r2 gcc=14.2.0-r4 musl-dev=1.2.5-r9 gcompat=1.1.0-r4 && \
    bundle install

FROM ruby:3.4.4-alpine3.21@sha256:5bdfa2312f3f0bc9bd077e954c943652155a093db228af978873ff92389300d3
ARG EMAIL_PORT="8080"

COPY --from=builder /usr/local/bundle/ /usr/local/bundle/

WORKDIR /email_server

COPY views/ views/

COPY .ruby-version .ruby-version
COPY Gemfile Gemfile
COPY Gemfile.lock Gemfile.lock
COPY email_server.rb email_server.rb

ENV EMAIL_PORT=${EMAIL_PORT}

EXPOSE ${EMAIL_PORT}
ENTRYPOINT ["bundle", "exec", "ruby", "email_server.rb"]
