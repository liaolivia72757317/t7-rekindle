FROM eclipse-temurin:25-jre-noble

WORKDIR /app
COPY --chown=10001:10001 target/rekindle-server-*.jar server.jar

ENV SERVER_ADDRESS=0.0.0.0
EXPOSE 8080
USER 10001:10001

ENTRYPOINT ["java", "-jar", "/app/server.jar"]
