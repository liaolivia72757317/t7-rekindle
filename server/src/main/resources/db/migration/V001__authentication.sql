CREATE TABLE account (
    account_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    login_name VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    auth_epoch BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL DEFAULT (UTC_TIMESTAMP(3)),
    CONSTRAINT chk_account_status CHECK (status IN ('ACTIVE', 'DISABLED'))
) ENGINE=InnoDB;

CREATE TABLE auth_session (
    session_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    account_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL UNIQUE,
    auth_epoch BIGINT NOT NULL,
    created_at DATETIME(3) NOT NULL,
    expires_at DATETIME(3) NOT NULL,
    revoked_at DATETIME(3),
    revoke_reason VARCHAR(32),
    UNIQUE KEY uq_session_account (session_id, account_id),
    KEY ix_session_account_created (account_id, created_at),
    CONSTRAINT fk_session_account FOREIGN KEY (account_id) REFERENCES account(account_id),
    CONSTRAINT chk_session_expiry CHECK (expires_at > created_at)
) ENGINE=InnoDB;

CREATE TABLE account_presence (
    account_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    session_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    connection_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    lease_expires_at DATETIME(3) NOT NULL,
    CONSTRAINT fk_presence_account FOREIGN KEY (account_id) REFERENCES account(account_id),
    CONSTRAINT fk_presence_session FOREIGN KEY (session_id, account_id)
        REFERENCES auth_session(session_id, account_id)
) ENGINE=InnoDB;
