SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
SET ANSI_PADDING ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET NUMERIC_ROUNDABORT OFF;
SET NOCOUNT ON;
SET XACT_ABORT ON;
USE master;

DECLARE @targetLogMb bigint = $(SimpleRecoveryLogMB);
DECLARE @db sysname, @sql nvarchar(max);
DECLARE dbs CURSOR LOCAL FAST_FORWARD FOR
    SELECT d.name
    FROM STRING_SPLIT(N'$(SimpleRecoveryDatabases)', N',') s
    JOIN sys.databases d ON d.name = LTRIM(RTRIM(s.value))
    WHERE d.database_id > 4 AND d.state_desc = N'ONLINE' AND d.is_read_only = 0;
OPEN dbs;
FETCH NEXT FROM dbs INTO @db;
WHILE @@FETCH_STATUS = 0
BEGIN
    IF EXISTS (SELECT 1 FROM sys.databases WHERE name = @db AND recovery_model_desc <> N'SIMPLE')
    BEGIN
        SET @sql = CONCAT(N'ALTER DATABASE ', QUOTENAME(@db), N' SET RECOVERY SIMPLE;');
        EXEC (@sql);
        PRINT CONCAT(N'set recovery of ', @db, N' to SIMPLE');
    END
    ELSE
        PRINT CONCAT(N'ok  recovery of ', @db, N' is SIMPLE');

    SET @sql = CONCAT(N'USE ', QUOTENAME(@db), N';
CHECKPOINT;
DECLARE @file sysname, @currentMb bigint, @afterMb bigint;
DECLARE logs CURSOR LOCAL FAST_FORWARD FOR
    SELECT name, CONVERT(bigint, size) / 128 FROM sys.database_files WHERE type_desc = N''LOG'';
OPEN logs;
FETCH NEXT FROM logs INTO @file, @currentMb;
WHILE @@FETCH_STATUS = 0
BEGIN
    IF @currentMb > @targetLogMb
    BEGIN
        DBCC SHRINKFILE (@file, @targetLogMb) WITH NO_INFOMSGS;
        SELECT @afterMb = CONVERT(bigint, size) / 128 FROM sys.database_files WHERE name = @file;
        IF @afterMb < @currentMb
            PRINT CONCAT(N''shrank log '', @file, N'': '', @currentMb, N'' MB -> '', @afterMb, N'' MB'');
        ELSE
            PRINT CONCAT(N''ok  log '', @file, N'' is '', @currentMb, N'' MB, the nearest virtual log file boundary above '', @targetLogMb, N'' MB'');
    END
    ELSE
        PRINT CONCAT(N''ok  log '', @file, N'' is '', @currentMb, N'' MB'');
    FETCH NEXT FROM logs INTO @file, @currentMb;
END;
CLOSE logs;
DEALLOCATE logs;');
    EXEC sys.sp_executesql @sql, N'@targetLogMb bigint', @targetLogMb = @targetLogMb;
    FETCH NEXT FROM dbs INTO @db;
END;
CLOSE dbs;
DEALLOCATE dbs;

SELECT d.name, d.recovery_model_desc, d.log_reuse_wait_desc, CONVERT(bigint, mf.size) / 128 AS log_mb
FROM sys.databases d
JOIN sys.master_files mf ON mf.database_id = d.database_id AND mf.type_desc = N'LOG'
WHERE d.name IN (SELECT LTRIM(RTRIM(value)) FROM STRING_SPLIT(N'$(SimpleRecoveryDatabases)', N','))
ORDER BY d.name;
