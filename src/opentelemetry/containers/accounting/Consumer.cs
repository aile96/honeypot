// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Confluent.Kafka;
using Microsoft.Extensions.Logging;
using Oteldemo;
using System.Linq;

namespace Accounting;

internal class Consumer : IDisposable
{
    private const string TopicName = "orders";

    private readonly ILogger<Consumer> _logger;
    private readonly IConsumer<string, byte[]> _consumer;
    private bool _isListening;

    public Consumer(ILogger<Consumer> logger)
    {
        _logger = logger;

        var servers = Environment.GetEnvironmentVariable("KAFKA_ADDR")
            ?? throw new ArgumentNullException("KAFKA_ADDR");

        _consumer = BuildConsumer(servers);
        _consumer.Subscribe(TopicName);

        _logger.LogInformation(
            "Kafka consumer initialized. topic={Topic} group_id={GroupId} bootstrap_servers={BootstrapServers}",
            TopicName,
            "accounting",
            servers);
    }

    public void StartListening()
    {
        _isListening = true;
        _logger.LogInformation("Starting Kafka consume loop for topic={Topic}", TopicName);

        try
        {
            while (_isListening)
            {
                try
                {
                    var consumeResult = _consumer.Consume();

                    _logger.LogDebug(
                        "Kafka message received. topic={Topic} partition={Partition} offset={Offset}",
                        consumeResult.Topic,
                        consumeResult.Partition.Value,
                        consumeResult.Offset.Value);

                    ProcessMessage(consumeResult.Message);
                }
                catch (ConsumeException e)
                {
                    _logger.LogError(
                        e,
                        "Kafka consume failed. topic={Topic} reason={Reason}",
                        TopicName,
                        e.Error.Reason);
                }
            }
        }
        catch (OperationCanceledException)
        {
            _logger.LogInformation("Consume loop cancelled, closing Kafka consumer");

            _consumer.Close();
        }
    }

    private void ProcessMessage(Message<string, byte[]> message)
    {
        try
        {
            var order = OrderResult.Parser.ParseFrom(message.Value);
            var itemCount = order.Items.Count;
            var totalUnits = order.Items.Sum(x => x.Item?.Quantity ?? 0);

            Log.OrderReceivedMessage(_logger, order.OrderId, itemCount, totalUnits);
        }
        catch (Exception ex)
        {
            _logger.LogError(
                ex,
                "Order parsing failed. payload_size_bytes={PayloadSize}",
                message.Value?.Length ?? 0);
        }
    }

    private IConsumer<string, byte[]> BuildConsumer(string servers)
    {
        var conf = new ConsumerConfig
        {
            GroupId = $"accounting",
            BootstrapServers = servers,
            // https://github.com/confluentinc/confluent-kafka-dotnet/tree/07de95ed647af80a0db39ce6a8891a630423b952#basic-consumer-example
            AutoOffsetReset = AutoOffsetReset.Earliest,
            EnableAutoCommit = true
        };

        return new ConsumerBuilder<string, byte[]>(conf)
            .Build();
    }

    public void Dispose()
    {
        _isListening = false;
        _consumer?.Dispose();
    }
}
