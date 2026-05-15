// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
const grpc = require('@grpc/grpc-js')
const protoLoader = require('@grpc/proto-loader')
const health = require('grpc-js-health-check')
const opentelemetry = require('@opentelemetry/api')

const charge = require('./charge')
const logger = require('./logger')
const { upsertPaymentMethod, getPaymentMethod } = require('./db')

async function chargeServiceHandler(call, callback) {
  const span = opentelemetry.trace.getActiveSpan();

  try {
    const amount = call.request.amount
    span?.setAttributes({
      'app.payment.amount': parseFloat(`${amount.units}.${amount.nanos}`).toFixed(2)
    })
    logger.info({
      step: 'charge.request_received',
      amount: {
        currencyCode: amount?.currencyCode,
        units: amount?.units,
        nanos: amount?.nanos
      }
    }, 'Charge request received')

    const response = await charge.charge(call.request)
    logger.info({
      step: 'charge.response_ready',
      transactionId: response?.transactionId
    }, 'Charge request completed')
    callback(null, response)

  } catch (err) {
    logger.warn({ err, step: 'charge.failed' }, 'Charge request failed')

    span?.recordException(err)
    span?.setStatus({ code: opentelemetry.SpanStatusCode.ERROR })
    callback(err)
  }
}

async function addPaymentHandler(call, callback) {
  try {
    const p = call.request?.payment
    logger.info({
      step: 'payment_method.save.request_received',
      userId: p?.userId
    }, 'AddPayment request received')
    if (!p || !p.userId || !p.cardHolderName || !p.cardNumber || !p.expMonth || !p.expYear || !p.cvv) {
      logger.warn({ step: 'payment_method.save.validation_failed', userId: p?.userId }, 'Missing AddPayment parameters')
      return callback(null, { ok: false, message: 'Missing parameters' })
    }
    const saved = await upsertPaymentMethod({
      userId: p.userId,
      cardHolderName: p.cardHolderName,
      cardNumber: p.cardNumber,
      expMonth: p.expMonth,
      expYear: p.expYear,
      cvv: p.cvv
    })
    logger.info({ userId: saved.user_id, last4: String(saved.card_number).slice(-4) }, 'Payment saved')
    return callback(null, {
      ok: true,
      message: 'Payment method saved',
      user_id: saved.user_id,
      last4: String(saved.card_number).slice(-4)
    })
  } catch (err) {
    logger.warn({ err, step: 'payment_method.save.failed' }, 'AddPayment error')
    return callback({ code: grpc.status.INTERNAL, message: 'Internal error' })
  }
}

async function receivePaymentHandler(call, callback) {
  try {
    const userId = call.request?.user_id || call.request?.userId // both ok
    logger.info({ step: 'payment_method.retrieve.request_received', userId }, 'ReceivePayment request received')
    if (!userId) return callback(null, { found: false, error: 'userId mancante' })
    const pm = await getPaymentMethod(userId)
    if (!pm) return callback(null, { found: false, error: 'No payment for userId' })
    logger.info({
      step: 'payment_method.retrieve.found',
      userId: pm.user_id,
      last4: String(pm.card_number).slice(-4)
    }, 'Payment method found')
    return callback(null, {
      found: true,
      payment: {
        userId: pm.user_id,
        cardHolderName: pm.card_holder_name,
        cardNumber: pm.card_number,
        expMonth: pm.exp_month,
        expYear: pm.exp_year,
        cvv: pm.cvv
      }
    })
  } catch (err) {
    logger.warn({ err, step: 'payment_method.retrieve.failed' }, 'ReceivePayment error')
    return callback({ code: grpc.status.INTERNAL, message: 'Internal error' })
  }
}

async function closeGracefully(signal) {
  server.forceShutdown()
  process.kill(process.pid, signal)
}

const otelDemoPackage = grpc.loadPackageDefinition(protoLoader.loadSync('demo.proto'))
const server = new grpc.Server()

server.addService(health.service, new health.Implementation({
  '': health.servingStatus.SERVING
}))

server.addService(otelDemoPackage.oteldemo.PaymentService.service, {
  charge: chargeServiceHandler,
  addPayment: addPaymentHandler,
  receivePayment: receivePaymentHandler
})

server.bindAsync(`0.0.0.0:${process.env['PAYMENT_PORT']}`, grpc.ServerCredentials.createInsecure(), (err, port) => {
  if (err) {
    return logger.error({ err, step: 'grpc_server.bind_failed' }, 'payment gRPC bind failed')
  }

  logger.info({ step: 'grpc_server.started', port }, 'payment gRPC server started')
})

process.once('SIGINT', closeGracefully)
process.once('SIGTERM', closeGracefully)
