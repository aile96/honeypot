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
    logger.info({ request: call.request }, "Charge request received.")

    const response = await charge.charge(call.request)
    callback(null, response)

  } catch (err) {
    logger.warn({ err })

    span?.recordException(err)
    span?.setStatus({ code: opentelemetry.SpanStatusCode.ERROR })
    callback(err)
  }
}

async function addPaymentHandler(call, callback) {
  try {
    const p = call.request?.payment
    if (!p || !p.userId || !p.cardHolderName || !p.cardNumber || !p.expMonth || !p.expYear || !p.cvv) {
      return callback(null, { ok: false, message: 'Parametri mancanti' })
    }
    const saved = await upsertPaymentMethod({
      userId: p.userId,
      cardHolderName: p.cardHolderName,
      cardNumber: p.cardNumber,
      expMonth: p.expMonth,
      expYear: p.expYear,
      cvv: p.cvv
    })
    // Evita di mettere dati sensibili nei log
    logger.info({ userId: saved.user_id, last4: String(saved.card_number).slice(-4) }, 'Payment salvato')
    return callback(null, {
      ok: true,
      message: 'Metodo di pagamento salvato',
      user_id: saved.user_id,
      last4: String(saved.card_number).slice(-4)
    })
  } catch (err) {
    logger.warn({ err }, 'Errore AddPayment')
    return callback({ code: grpc.status.INTERNAL, message: 'Errore interno' })
  }
}

async function receivePaymentHandler(call, callback) {
  try {
    const userId = call.request?.user_id || call.request?.userId // tollera entrambi
    if (!userId) return callback(null, { found: false, error: 'userId mancante' })
    const pm = await getPaymentMethod(userId)
    if (!pm) return callback(null, { found: false, error: 'Nessun pagamento per userId' })
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
    logger.warn({ err }, 'Errore ReceivePayment')
    return callback({ code: grpc.status.INTERNAL, message: 'Errore interno' })
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
    return logger.error({ err })
  }

  logger.info(`payment gRPC server started on port ${port}`)
})

process.once('SIGINT', closeGracefully)
process.once('SIGTERM', closeGracefully)
