# Self-hosted GitHub Actions runner for private repositories, run on Railway.
# Railway containers cannot start Docker, so job `services:` containers are not
# available; PostgreSQL 17 is installed here and started by the job instead.
FROM myoung34/github-runner:ubuntu-noble

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg lsb-release \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-17 \
 && rm -rf /var/lib/apt/lists/*

# Disposable test database: local connections only, trust auth, no secrets.
RUN sed -i "s/^#\?listen_addresses.*/listen_addresses = '127.0.0.1'/" /etc/postgresql/17/main/postgresql.conf \
 && printf 'local all all trust\nhost all all 127.0.0.1/32 trust\nhost all all ::1/128 trust\n' \
      > /etc/postgresql/17/main/pg_hba.conf

COPY start-postgres.sh /usr/local/bin/start-postgres
RUN chmod +x /usr/local/bin/start-postgres

# Runner slots for multi-runner.sh, hard-linked here at build time: one set of inodes in this layer,
# so the runners share page cache. Linking at runtime instead copies every file up into the
# container layer (overlayfs) and costs ~1 GB of billed memory for four runners.
RUN mkdir -p /opt/runners && for i in 1 2 3 4 5 6 7 8; do cp -al /actions-runner /opt/runners/slot$i; done
COPY multi-runner.sh /usr/local/bin/multi-runner
COPY entrypoint.sh /usr/local/bin/railway-entrypoint
RUN chmod +x /usr/local/bin/multi-runner /usr/local/bin/railway-entrypoint
ENTRYPOINT ["/usr/local/bin/railway-entrypoint"]
CMD ["./bin/Runner.Listener", "run", "--startuptype", "service"]
