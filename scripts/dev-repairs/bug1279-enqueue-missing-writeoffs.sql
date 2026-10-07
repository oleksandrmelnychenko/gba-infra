SET NOCOUNT ON; SET XACT_ABORT ON;
BEGIN TRAN;
INSERT INTO SalesDurableEffectOutbox (EventNetUid, OperationNetUid, SequenceNumber, EffectType, SaleID, SaleNetUid, ResourceName, AttemptCount, AvailableAt, Created, Updated)
SELECT NEWID(), a.OperationNetUid, 32767, 'sale:consignment-movement', s.ID, s.NetUID, '', 0, SYSUTCDATETIME(), SYSUTCDATETIME(), SYSUTCDATETIME()
FROM Sale s
CROSS APPLY (SELECT TOP 1 e.OperationNetUid FROM SalesDurableEffectOutbox e JOIN SalesMutationOperation op ON op.OperationNetUid=e.OperationNetUid AND op.IsCompleted=1 AND op.SaleID=s.ID WHERE e.EffectType='sale:lifecycle-audit' AND e.SaleNetUid=s.NetUID AND e.NewLifecycle=1 ORDER BY e.ID) a
JOIN BaseLifeCycleStatus b ON b.ID=s.BaseLifeCycleStatusID
WHERE s.ID IN (1545796,1545797,1545798,1545799) AND s.Deleted=0 AND s.IsImported=0 AND s.IsAcceptedToPacking=1 AND b.SaleLifeCycleType=1
 AND NOT EXISTS (SELECT 1 FROM SalesDurableEffectOutbox x WHERE x.EffectType='sale:consignment-movement' AND (x.SaleID=s.ID OR x.SaleNetUid=s.NetUID));
SELECT @@ROWCOUNT AS inserted;
COMMIT;
