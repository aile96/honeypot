// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Accounting;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

var host = Host.CreateDefaultBuilder(args)
    .ConfigureServices(services =>
    {
        services.AddSingleton<Consumer>();
    })
    .Build();

var startupLogger = host.Services.GetRequiredService<ILoggerFactory>().CreateLogger("Accounting.Startup");
startupLogger.LogInformation("Accounting service startup sequence initiated");

var consumer = host.Services.GetRequiredService<Consumer>();
startupLogger.LogInformation("Kafka consumer resolved, starting consume loop");
consumer.StartListening();

host.Run();
