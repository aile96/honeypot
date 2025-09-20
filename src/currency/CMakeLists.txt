cmake_minimum_required(VERSION 3.16)
project(currency CXX)

# Minimal but important tweaks
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

find_package(Protobuf REQUIRED)                     # protobuf::libprotobuf
find_package(gRPC CONFIG REQUIRED)                  # gRPC::grpc++
find_package(opentelemetry-cpp CONFIG REQUIRED)     # ${OPENTELEMETRY_CPP_LIBRARIES}
find_package(PostgreSQL REQUIRED)                   # PostgreSQL::PostgreSQL (libpq)

# Where your generated *.pb.* end up (keep as in your setup)
set(GENERATED_PROTOBUF_PATH "${CMAKE_BINARY_DIR}/generated/proto")

add_subdirectory(genproto)  # must define target: demo-proto

# Your service
add_executable(currency src/server.cpp)
add_dependencies(currency demo-proto)

# Include the generated headers
target_include_directories(currency PRIVATE
  "${GENERATED_PROTOBUF_PATH}"
  "${CMAKE_CURRENT_SOURCE_DIR}/src"
  "${CMAKE_CURRENT_SOURCE_DIR}/src/third_party"
)

target_sources(currency PRIVATE
  src/flagd_client.cpp
  src/file_mirror_http.cpp
)

# Link everything (keeps your existing OTel targets)
target_link_libraries(currency PRIVATE
  demo-proto
  protobuf::libprotobuf
  gRPC::grpc++
  PostgreSQL::PostgreSQL

  ${OPENTELEMETRY_CPP_LIBRARIES}
  opentelemetry_trace
  opentelemetry_common
  opentelemetry_exporter_otlp_grpc
  opentelemetry_exporter_otlp_grpc_client
  opentelemetry_proto
  opentelemetry_otlp_recordable
  opentelemetry_resources
  opentelemetry_metrics
  opentelemetry_exporter_otlp_grpc_metrics
)

install(TARGETS currency DESTINATION bin)