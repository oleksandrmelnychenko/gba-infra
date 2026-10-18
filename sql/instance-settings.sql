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

DECLARE @settings TABLE (name nvarchar(128) NOT NULL PRIMARY KEY, target bigint NOT NULL);
INSERT INTO @settings (name, target)
VALUES
    (N'show advanced options', 1),
    (N'max degree of parallelism', $(MaxDop)),
    (N'cost threshold for parallelism', $(CostThreshold)),
    (N'max server memory (MB)', $(MaxServerMemoryMB)),
    (N'optimize for ad hoc workloads', $(OptimizeForAdHoc));

DECLARE @name nvarchar(128), @target bigint, @current bigint, @changed bit = 0;

SELECT @current = CONVERT(bigint, value_in_use) FROM sys.configurations WHERE name = N'show advanced options';
IF @current <> 1
BEGIN
    EXEC sys.sp_configure N'show advanced options', 1;
    RECONFIGURE;
END;

DECLARE settings CURSOR LOCAL FAST_FORWARD FOR SELECT name, target FROM @settings WHERE name <> N'show advanced options';
OPEN settings;
FETCH NEXT FROM settings INTO @name, @target;
WHILE @@FETCH_STATUS = 0
BEGIN
    SELECT @current = CONVERT(bigint, value) FROM sys.configurations WHERE name = @name;
    IF @current <> @target
    BEGIN
        EXEC sys.sp_configure @name, @target;
        SET @changed = 1;
        PRINT CONCAT(N'set ', @name, N': ', @current, N' -> ', @target);
    END
    ELSE
        PRINT CONCAT(N'ok  ', @name, N' = ', @target);
    FETCH NEXT FROM settings INTO @name, @target;
END;
CLOSE settings;
DEALLOCATE settings;

IF @changed = 1
    RECONFIGURE;

DECLARE @cpu int = (SELECT cpu_count FROM sys.dm_os_sys_info);
DECLARE @wantFiles int = CASE WHEN @cpu < $(TempdbFileCount) THEN @cpu ELSE $(TempdbFileCount) END;
DECLARE @haveFiles int = (SELECT COUNT(*) FROM sys.master_files WHERE database_id = 2 AND type_desc = N'ROWS');
DECLARE @dataPath nvarchar(260) = (SELECT TOP (1) LEFT(physical_name, LEN(physical_name) - CHARINDEX(N'/', REVERSE(physical_name)) + 1) FROM sys.master_files WHERE database_id = 2 AND type_desc = N'ROWS' ORDER BY file_id);
DECLARE @sql nvarchar(max), @i int;

SET @i = @haveFiles + 1;
WHILE @i <= @wantFiles
BEGIN
    SET @sql = CONCAT(N'ALTER DATABASE tempdb ADD FILE (NAME = N''tempdev', @i, N''', FILENAME = N''', @dataPath, N'tempdb', @i, N'.ndf'', SIZE = $(TempdbFileSizeMB)MB, FILEGROWTH = $(TempdbFileGrowthMB)MB);');
    EXEC (@sql);
    PRINT CONCAT(N'added tempdb data file ', @i);
    SET @i += 1;
END;

DECLARE @file nvarchar(128), @type nvarchar(60), @startupMb bigint, @currentMb bigint, @growthMb bigint, @isPercent bit, @targetMb bigint;
DECLARE files CURSOR LOCAL FAST_FORWARD FOR
    SELECT mf.name, mf.type_desc, CONVERT(bigint, mf.size) / 128, CONVERT(bigint, df.size) / 128, CONVERT(bigint, mf.growth) / 128, mf.is_percent_growth
    FROM sys.master_files mf
    JOIN tempdb.sys.database_files df ON df.file_id = mf.file_id
    WHERE mf.database_id = 2;
OPEN files;
FETCH NEXT FROM files INTO @file, @type, @startupMb, @currentMb, @growthMb, @isPercent;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @targetMb = CASE WHEN @type = N'LOG' THEN $(TempdbLogSizeMB) ELSE $(TempdbFileSizeMB) END;
    IF @startupMb < @targetMb AND @currentMb < @targetMb
    BEGIN
        SET @sql = CONCAT(N'ALTER DATABASE tempdb MODIFY FILE (NAME = N''', @file, N''', SIZE = ', @targetMb, N'MB);');
        EXEC (@sql);
        PRINT CONCAT(N'sized tempdb file ', @file, N': ', @currentMb, N' MB -> ', @targetMb, N' MB');
    END
    ELSE IF @startupMb < @targetMb
        PRINT CONCAT(N'warn tempdb file ', @file, N' is ', @currentMb, N' MB now but starts at ', @startupMb, N' MB; restart, then rerun to pre-size it');
    ELSE
        PRINT CONCAT(N'ok  tempdb file ', @file, N' starts at ', @startupMb, N' MB');
    IF @isPercent = 1 OR @growthMb <> $(TempdbFileGrowthMB)
    BEGIN
        SET @sql = CONCAT(N'ALTER DATABASE tempdb MODIFY FILE (NAME = N''', @file, N''', FILEGROWTH = $(TempdbFileGrowthMB)MB);');
        EXEC (@sql);
        PRINT CONCAT(N'set growth of tempdb file ', @file, N' to $(TempdbFileGrowthMB) MB');
    END;
    FETCH NEXT FROM files INTO @file, @type, @startupMb, @currentMb, @growthMb, @isPercent;
END;
CLOSE files;
DEALLOCATE files;

DECLARE @db sysname;
DECLARE dbs CURSOR LOCAL FAST_FORWARD FOR
    SELECT d.name
    FROM STRING_SPLIT(N'$(QueryStoreDatabases)', N',') s
    JOIN sys.databases d ON d.name = LTRIM(RTRIM(s.value))
    WHERE d.state_desc = N'ONLINE' AND d.is_read_only = 0;
OPEN dbs;
FETCH NEXT FROM dbs INTO @db;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @sql = CONCAT(N'ALTER DATABASE ', QUOTENAME(@db), N' SET QUERY_STORE = ON (OPERATION_MODE = READ_WRITE, QUERY_CAPTURE_MODE = AUTO, MAX_STORAGE_SIZE_MB = $(QueryStoreMaxMB), INTERVAL_LENGTH_MINUTES = 15, DATA_FLUSH_INTERVAL_SECONDS = 900, CLEANUP_POLICY = (STALE_QUERY_THRESHOLD_DAYS = 30), SIZE_BASED_CLEANUP_MODE = AUTO, MAX_PLANS_PER_QUERY = 200, WAIT_STATS_CAPTURE_MODE = ON);');
    EXEC (@sql);
    PRINT CONCAT(N'ok  query store read_write on ', @db);
    FETCH NEXT FROM dbs INTO @db;
END;
CLOSE dbs;
DEALLOCATE dbs;

SELECT name, CONVERT(bigint, value_in_use) AS value_in_use
FROM sys.configurations
WHERE name IN (N'max degree of parallelism', N'cost threshold for parallelism', N'max server memory (MB)', N'optimize for ad hoc workloads')
ORDER BY name;

SELECT mf.name, mf.type_desc, CONVERT(bigint, mf.size) / 128 AS startup_mb, CONVERT(bigint, df.size) / 128 AS current_mb, CONVERT(bigint, mf.growth) / 128 AS growth_mb
FROM sys.master_files mf
JOIN tempdb.sys.database_files df ON df.file_id = mf.file_id
WHERE mf.database_id = 2
ORDER BY mf.file_id;

DECLARE @qs nvarchar(max) = N'';
SELECT @qs = @qs + CASE WHEN @qs = N'' THEN N'' ELSE N' UNION ALL ' END
    + CONCAT(N'SELECT N''', d.name, N''' AS database_name, actual_state_desc, query_capture_mode_desc, max_storage_size_mb, current_storage_size_mb, interval_length_minutes FROM ', QUOTENAME(d.name), N'.sys.database_query_store_options')
FROM sys.databases d
WHERE d.name IN (SELECT LTRIM(RTRIM(value)) FROM STRING_SPLIT(N'$(QueryStoreDatabases)', N','))
  AND d.state_desc = N'ONLINE';
IF @qs <> N''
    EXEC (@qs);
