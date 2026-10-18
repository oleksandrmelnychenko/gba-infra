SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
SET ANSI_PADDING ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET NUMERIC_ROUNDABORT OFF;
SET NOCOUNT ON;
SET DEADLOCK_PRIORITY LOW;
SET LOCK_TIMEOUT 60000;

DECLARE @deadline datetime2(0) = DATEADD(MINUTE, $(MaxMinutes), SYSUTCDATETIME());
DECLARE @started datetime2(3) = SYSUTCDATETIME();
DECLARE @pause char(12) = CONVERT(char(12), DATEADD(MILLISECOND, $(PauseMs), CONVERT(datetime, 0)), 114);
DECLARE @syncLockFree int = 1;
DECLARE @syncLockCheck nvarchar(300) =
    N'SELECT @free = APPLOCK_TEST(N''public'', N''GBA_DATASYNC_GLOBAL_LOCK'', N''Shared'', N''Session'');';

IF DB_ID(N'$(SyncLockDatabase)') IS NOT NULL
    EXEC [$(SyncLockDatabase)].sys.sp_executesql @syncLockCheck, N'@free int OUTPUT', @free = @syncLockFree OUTPUT;
IF @syncLockFree = 0
BEGIN
    PRINT CONCAT(N'statistics ', DB_NAME(), N': skipped, a data sync holds GBA_DATASYNC_GLOBAL_LOCK');
    RETURN;
END;

IF OBJECT_ID('tempdb..#work') IS NOT NULL DROP TABLE #work;

SELECT
    QUOTENAME(SCHEMA_NAME(o.schema_id)) + N'.' + QUOTENAME(o.name) AS table_name,
    QUOTENAME(s.name) AS stats_name,
    s.has_filter,
    sp.rows,
    sp.modification_counter,
    sp.last_updated,
    CASE
        WHEN sp.last_updated IS NULL THEN 1
        WHEN s.has_filter = 1 AND sp.modification_counter > 0 THEN 2
        ELSE 3
    END AS priority
INTO #work
FROM sys.stats s
JOIN sys.objects o ON o.object_id = s.object_id
CROSS APPLY sys.dm_db_stats_properties(s.object_id, s.stats_id) sp
WHERE o.is_ms_shipped = 0
  AND (o.type = 'U' OR (o.type = 'V' AND OBJECTPROPERTY(o.object_id, 'IsIndexed') = 1))
  AND (
        (sp.last_updated IS NULL AND EXISTS (SELECT 1 FROM sys.partitions p WHERE p.object_id = o.object_id AND p.index_id IN (0, 1) AND p.rows > 0))
     OR (s.has_filter = 1 AND sp.modification_counter > 0)
     OR (sp.modification_counter >= $(MinModifiedRows) AND sp.modification_counter * 100.0 >= sp.rows * $(ModifiedPercent))
  );

DECLARE @candidates int = (SELECT COUNT(*) FROM #work);
DECLARE @done int = 0, @failed int = 0, @skipped int = 0;
DECLARE @table nvarchar(300), @stats nvarchar(300), @rows bigint, @mods bigint, @sql nvarchar(max), @t0 datetime2(3);

DECLARE work CURSOR LOCAL FAST_FORWARD FOR
    SELECT table_name, stats_name, rows, modification_counter
    FROM #work
    ORDER BY priority, modification_counter DESC;
OPEN work;
FETCH NEXT FROM work INTO @table, @stats, @rows, @mods;
WHILE @@FETCH_STATUS = 0
BEGIN
    IF @syncLockFree = 1 AND DB_ID(N'$(SyncLockDatabase)') IS NOT NULL
        EXEC [$(SyncLockDatabase)].sys.sp_executesql @syncLockCheck, N'@free int OUTPUT', @free = @syncLockFree OUTPUT;
    IF SYSUTCDATETIME() >= @deadline OR @syncLockFree = 0
    BEGIN
        SET @skipped += 1;
    END
    ELSE
    BEGIN
        SET @sql = N'UPDATE STATISTICS ' + @table + N' ' + @stats + N' WITH '
            + CASE
                WHEN N'$(ScanMode)' = N'DEFAULT' THEN N''
                WHEN ISNULL(@rows, 0) <= $(FullScanRowLimit) THEN N'FULLSCAN, '
                ELSE N'SAMPLE 25 PERCENT, ' END
            + N'MAXDOP = $(StatsMaxDop);';
        SET @t0 = SYSUTCDATETIME();
        BEGIN TRY
            EXEC (@sql);
            SET @done += 1;
        END TRY
        BEGIN CATCH
            SET @failed += 1;
            PRINT CONCAT(N'fail ', @table, N' ', @stats, N': ', ERROR_NUMBER(), N' ', ERROR_MESSAGE());
        END CATCH;
        IF DATEDIFF(MILLISECOND, @t0, SYSUTCDATETIME()) >= 5000
            PRINT CONCAT(N'slow ', @table, N' ', @stats, N' rows=', @rows, N' mods=', @mods, N' ms=', DATEDIFF(MILLISECOND, @t0, SYSUTCDATETIME()));
        IF $(PauseMs) > 0
            WAITFOR DELAY @pause;
    END;
    FETCH NEXT FROM work INTO @table, @stats, @rows, @mods;
END;
CLOSE work;
DEALLOCATE work;

PRINT CONCAT(N'statistics ', DB_NAME(), N': candidates=', @candidates, N' updated=', @done, N' failed=', @failed,
    N' deferred=', @skipped, CASE WHEN @syncLockFree = 0 THEN N' (stopped: a data sync started)' ELSE N'' END,
    N' seconds=', DATEDIFF(SECOND, @started, SYSUTCDATETIME()));

IF @failed > 0 AND @done = 0
    RAISERROR(N'statistics maintenance updated nothing and had failures', 16, 1);
