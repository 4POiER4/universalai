FROM postgres:16-alpine

RUN apk add --no-cache gnupg
COPY backup.sh /usr/local/bin/backup-messenger
COPY restore.sh /usr/local/bin/restore-messenger
RUN chmod 0555 /usr/local/bin/backup-messenger /usr/local/bin/restore-messenger

ENTRYPOINT ["/usr/local/bin/backup-messenger"]
