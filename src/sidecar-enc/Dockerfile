# build
FROM golang:1.24 AS build
WORKDIR /src

# 1) copy mod e scarica le dipendenze base
COPY go.mod ./
RUN go mod download

# 2) copia il resto del codice, genera go.sum e compila
COPY . .
RUN go mod tidy
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags="-s -w" -o /out/crypto-proxy .

# run (distroless)
FROM gcr.io/distroless/static-debian12
COPY --from=build /out/crypto-proxy /crypto-proxy
EXPOSE 18080 8081
ENTRYPOINT ["/crypto-proxy"]
