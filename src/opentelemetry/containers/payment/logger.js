// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

const pino = require('pino')

const logger = pino({
    level: process.env.LOG_LEVEL || 'info',
    timestamp: pino.stdTimeFunctions.isoTime,
    redact: {
      paths: [
        'request.creditCard.creditCardNumber',
        'request.creditCard.creditCardCvv',
        'request.creditCard.credit_card_number',
        'request.creditCard.credit_card_cvv',
        'request.payment.cardNumber',
        'request.payment.cvv',
        'creditCardNumber',
        'creditCardCvv',
        'cardNumber',
        'cvv'
      ],
      remove: true,
    },
    mixin() {
      return {
        'service.name': process.env['OTEL_SERVICE_NAME'],
      }  
    },
    formatters: {
        level: (label) => {
          return { 'level': label };
        },
      },
});

module.exports = logger;
