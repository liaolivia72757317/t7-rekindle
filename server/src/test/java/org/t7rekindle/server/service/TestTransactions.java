package org.t7rekindle.server.service;

import org.springframework.transaction.support.SimpleTransactionStatus;
import org.springframework.transaction.support.TransactionCallback;
import org.springframework.transaction.support.TransactionTemplate;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

final class TestTransactions {
    private TestTransactions() { }
    static TransactionTemplate immediate() {
        var tx = mock(TransactionTemplate.class);
        when(tx.execute(any())).thenAnswer(call -> {
            TransactionCallback<?> callback = call.getArgument(0);
            return callback.doInTransaction(new SimpleTransactionStatus());
        });
        doCallRealMethod().when(tx).executeWithoutResult(any());
        return tx;
    }
}
