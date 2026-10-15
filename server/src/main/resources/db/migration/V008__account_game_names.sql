ALTER TABLE account
    ADD game_name VARCHAR(31) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL,
    ADD UNIQUE KEY uq_account_game_name (game_name);
