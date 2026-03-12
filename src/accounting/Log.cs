// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Microsoft.Extensions.Logging;

namespace Accounting
{
    internal static partial class Log
    {
        [LoggerMessage(
            Level = LogLevel.Information,
            Message = "Order processed from Kafka. order_id={OrderId} items={ItemCount} total_units={TotalUnits}.")]
        public static partial void OrderReceivedMessage(ILogger logger, string orderId, int itemCount, int totalUnits);
    }
}
