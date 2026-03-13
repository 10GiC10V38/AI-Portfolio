FROM golang:1.22-alpine AS builder

WORKDIR /app

# Copy module definition and source together so go mod tidy has access to code
COPY go.mod .
COPY *.go .
RUN go mod tidy
RUN CGO_ENABLED=0 GOOS=linux go build -o api-gateway .

FROM alpine:3.20
RUN apk --no-cache add ca-certificates
WORKDIR /app
COPY --from=builder /app/api-gateway .

EXPOSE 8080
CMD ["./api-gateway"]
