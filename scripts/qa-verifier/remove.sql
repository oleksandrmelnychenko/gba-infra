SET NOCOUNT ON;
SET XACT_ABORT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;

IF CONVERT(nvarchar(128), @@SERVERNAME) <> N'$(ServerIdentity)'
    THROW 54980, N'Refusing to run outside the approved DEV SQL instance.', 1;
IF NOT ((N'$(V5Db)' = N'ConcordDb_V5' AND N'$(IdentityDb)' = N'ConcordIdentityDb')
     OR (N'$(V5Db)' = N'ConcordDb_V5_QA' AND N'$(IdentityDb)' = N'ConcordIdentityDb_QA'))
    THROW 54981, N'Only the DEV pair or the QA pair of databases may be targeted.', 1;
IF N'$(V5Db)' = N'ConcordDb_V5_QA' AND (
    NOT EXISTS (
        SELECT 1 FROM [ConcordDb_V5_QA].sys.extended_properties
        WHERE class = 0 AND name = N'GbaQaStandDb'
          AND CONVERT(nvarchar(4000), value) LIKE N'GBA[_]QA[_]STAND|%')
    OR NOT EXISTS (
        SELECT 1 FROM [ConcordIdentityDb_QA].sys.extended_properties
        WHERE class = 0 AND name = N'GbaQaStandDb'
          AND CONVERT(nvarchar(4000), value) LIKE N'GBA[_]QA[_]STAND|%'))
    THROW 54982, N'QA databases do not carry the GbaQaStandDb marker.', 1;

DECLARE @RoleName nvarchar(80) = N'$(RoleName)';
DECLARE @Login nvarchar(256) = N'$(Login)';
DECLARE @Email nvarchar(256) = N'$(Email)';
DECLARE @Commit bit = $(Commit);

DECLARE @RoleId bigint = (
    SELECT ID FROM [$(V5Db)].dbo.UserRole
    WHERE Name = @RoleName AND UserRoleType = $(UserRoleType));
DECLARE @UserId bigint, @UserNetUid uniqueidentifier, @UserRoleId bigint;
SELECT @UserId = ID, @UserNetUid = NetUID, @UserRoleId = UserRoleID
FROM [$(V5Db)].dbo.[User]
WHERE Email = @Email AND PhoneNumber = @Login;

IF @UserId IS NOT NULL AND @UserRoleId IS NOT NULL AND (@RoleId IS NULL OR @UserRoleId <> @RoleId)
    THROW 54983, N'The verifier user is attached to a role other than the verifier role; refusing to remove.', 1;
IF @RoleId IS NOT NULL AND EXISTS (
    SELECT 1 FROM [$(V5Db)].dbo.[User] WHERE UserRoleID = @RoleId AND (@UserId IS NULL OR ID <> @UserId))
    THROW 54984, N'Other users hold the verifier role; refusing to remove.', 1;

DECLARE @IdentityUserId nvarchar(450) = (
    SELECT Id FROM [$(IdentityDb)].dbo.AspNetUsers
    WHERE UserName = @Login AND Email = @Email);

BEGIN TRANSACTION;

DECLARE @References bigint = 0, @UserAction nvarchar(20) = N'absent';
IF @UserId IS NOT NULL
BEGIN
    DELETE FROM [$(V5Db)].dbo.UserScreenResolution WHERE UserID = @UserId;
    DELETE FROM [$(V5Db)].dbo.ColumnItem WHERE UserID = @UserId;

    DECLARE @Sql nvarchar(max) = N'';
    SELECT @Sql = @Sql + N'SELECT @n = @n + COUNT_BIG(*) FROM [$(V5Db)].' + QUOTENAME(s.name) + N'.' + QUOTENAME(t.name)
        + N' WHERE ' + QUOTENAME(c.name) + N' = @UserId;' + NCHAR(10)
    FROM [$(V5Db)].sys.foreign_keys fk
    JOIN [$(V5Db)].sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
    JOIN [$(V5Db)].sys.tables t ON t.object_id = fk.parent_object_id
    JOIN [$(V5Db)].sys.schemas s ON s.schema_id = t.schema_id
    JOIN [$(V5Db)].sys.columns c ON c.object_id = fkc.parent_object_id AND c.column_id = fkc.parent_column_id
    WHERE fk.referenced_object_id = OBJECT_ID(N'[$(V5Db)].dbo.[User]');
    EXEC sys.sp_executesql @Sql, N'@UserId bigint, @n bigint OUTPUT', @UserId = @UserId, @n = @References OUTPUT;

    IF @References = 0
    BEGIN
        DELETE FROM [$(V5Db)].dbo.[User] WHERE ID = @UserId;
        SET @UserAction = N'deleted';
    END
    ELSE
    BEGIN
        UPDATE [$(V5Db)].dbo.[User] SET Deleted = 1, IsActive = 0, Updated = SYSUTCDATETIME() WHERE ID = @UserId;
        SET @UserAction = N'soft-deleted';
    END
END;

DECLARE @Grants int = 0, @RoleAction nvarchar(20) = N'absent';
IF @RoleId IS NOT NULL
BEGIN
    DELETE FROM [$(V5Db)].dbo.RolePermission WHERE UserRoleID = @RoleId;
    SET @Grants = @@ROWCOUNT;
    DELETE FROM [$(V5Db)].dbo.RoleEventPermissionRevision WHERE UserRoleID = @RoleId;
    DELETE FROM [$(V5Db)].dbo.RoleEventPermissionCatalogUpgrade WHERE UserRoleID = @RoleId;
    DELETE FROM [$(V5Db)].dbo.UserRoleTranslation WHERE UserRoleID = @RoleId;
    DELETE FROM [$(V5Db)].dbo.UserRoleDashboardNode WHERE UserRoleID = @RoleId;
    IF EXISTS (SELECT 1 FROM [$(V5Db)].dbo.[User] WHERE UserRoleID = @RoleId)
    BEGIN
        UPDATE [$(V5Db)].dbo.UserRole SET Deleted = 1, Updated = SYSUTCDATETIME() WHERE ID = @RoleId;
        SET @RoleAction = N'soft-deleted';
    END
    ELSE
    BEGIN
        DELETE FROM [$(V5Db)].dbo.UserRole WHERE ID = @RoleId;
        SET @RoleAction = N'deleted';
    END
END;

DECLARE @Tokens int = 0, @IdentityAction nvarchar(20) = N'absent', @IdentityRoleAction nvarchar(20) = N'absent';
IF @IdentityUserId IS NOT NULL
BEGIN
    DELETE FROM [$(IdentityDb)].dbo.UserToken WHERE UserID = @IdentityUserId;
    SET @Tokens = @@ROWCOUNT;
    DELETE FROM [$(IdentityDb)].dbo.AspNetUsers WHERE Id = @IdentityUserId;
    SET @IdentityAction = N'deleted';
END;
IF EXISTS (SELECT 1 FROM [$(IdentityDb)].dbo.AspNetRoles WHERE Name = @RoleName)
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM [$(IdentityDb)].dbo.AspNetUserRoles ur
        JOIN [$(IdentityDb)].dbo.AspNetRoles r ON r.Id = ur.RoleId
        WHERE r.Name = @RoleName)
    BEGIN
        DELETE FROM [$(IdentityDb)].dbo.AspNetRoles WHERE Name = @RoleName;
        SET @IdentityRoleAction = N'deleted';
    END
    ELSE
        SET @IdentityRoleAction = N'kept-in-use';
END;

IF @Commit = 1
    COMMIT TRANSACTION;
ELSE
    ROLLBACK TRANSACTION;

SELECT
    N'$(V5Db)' AS target,
    CASE WHEN @Commit = 1 THEN N'committed' ELSE N'rolled-back' END AS mode,
    @UserAction AS user_row,
    @References AS user_references,
    @RoleAction AS role_row,
    @Grants AS grants_removed,
    @IdentityAction AS identity_user,
    @Tokens AS refresh_rows_removed,
    @IdentityRoleAction AS identity_role;
