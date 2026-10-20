SET NOCOUNT ON;
SET XACT_ABORT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;

IF CONVERT(nvarchar(128), @@SERVERNAME) <> N'$(ServerIdentity)'
    THROW 54960, N'Refusing to run outside the approved DEV SQL instance.', 1;
IF DB_ID(N'ConcordDb_V5_QA') IS NULL
    THROW 54961, N'ConcordDb_V5_QA is missing.', 1;
IF NOT EXISTS (
    SELECT 1 FROM [ConcordDb_V5_QA].sys.extended_properties
    WHERE class = 0 AND name = N'GbaQaStandDb'
      AND CONVERT(nvarchar(4000), value) LIKE N'GBA[_]QA[_]STAND|%')
    THROW 54962, N'ConcordDb_V5_QA does not carry the GbaQaStandDb marker.', 1;

DECLARE @RoleNetUid uniqueidentifier = '$(RoleNetUid)';
DECLARE @UserNetUid uniqueidentifier = '$(UserNetUid)';
DECLARE @RoleName nvarchar(80) = N'$(RoleName)';
DECLARE @Now datetime2 = SYSUTCDATETIME();

DECLARE @DevRoleId bigint = (
    SELECT ID FROM [ConcordDb_V5].dbo.UserRole
    WHERE NetUID = @RoleNetUid AND Name = @RoleName AND Deleted = 0);
IF @DevRoleId IS NULL
    THROW 54963, N'The verifier role is missing on DEV.', 1;

DECLARE @DevUserId bigint = (
    SELECT ID FROM [ConcordDb_V5].dbo.[User]
    WHERE NetUID = @UserNetUid AND UserRoleID = @DevRoleId AND Deleted = 0);
IF @DevUserId IS NULL
    THROW 54964, N'The verifier user is missing on DEV.', 1;

IF EXISTS (
    SELECT 1 FROM [ConcordDb_V5_QA].dbo.UserRole
    WHERE Name = @RoleName AND NetUID <> @RoleNetUid AND Deleted = 0)
    THROW 54965, N'Another QA role already uses the verifier role name.', 1;

DECLARE @Desired TABLE (ControlId nvarchar(450) NOT NULL PRIMARY KEY, QaPermissionId bigint NULL);
INSERT INTO @Desired (ControlId, QaPermissionId)
SELECT DISTINCT dp.ControlId, qp.ID
FROM [ConcordDb_V5].dbo.RolePermission rp
JOIN [ConcordDb_V5].dbo.Permission dp
    ON dp.ID = rp.PermissionID AND dp.Deleted = 0 AND dp.Kind = N'Event'
LEFT JOIN [ConcordDb_V5_QA].dbo.Permission qp
    ON qp.ControlId = dp.ControlId AND qp.Kind = N'Event' AND qp.Deleted = 0
WHERE rp.UserRoleID = @DevRoleId AND rp.Deleted = 0;

IF EXISTS (SELECT 1 FROM @Desired WHERE QaPermissionId IS NULL)
    THROW 54966, N'The QA permission catalog lacks keys granted on DEV; refresh or redeploy QA first.', 1;

BEGIN TRANSACTION;

IF NOT EXISTS (SELECT 1 FROM [ConcordDb_V5_QA].dbo.UserRole WHERE NetUID = @RoleNetUid)
    INSERT INTO [ConcordDb_V5_QA].dbo.UserRole (Created, Dashboard, Deleted, Name, NetUID, Updated, UserRoleType)
    SELECT Created, Dashboard, 0, Name, NetUID, @Now, UserRoleType
    FROM [ConcordDb_V5].dbo.UserRole WHERE ID = @DevRoleId;
ELSE
    UPDATE q
    SET Name = d.Name, Dashboard = d.Dashboard, UserRoleType = d.UserRoleType, Deleted = 0, Updated = @Now
    FROM [ConcordDb_V5_QA].dbo.UserRole q
    JOIN [ConcordDb_V5].dbo.UserRole d ON d.NetUID = q.NetUID
    WHERE d.ID = @DevRoleId
      AND (q.Deleted = 1 OR q.Name <> d.Name OR ISNULL(q.Dashboard, N'') <> ISNULL(d.Dashboard, N'') OR q.UserRoleType <> d.UserRoleType);

DECLARE @QaRoleId bigint = (SELECT ID FROM [ConcordDb_V5_QA].dbo.UserRole WHERE NetUID = @RoleNetUid);

UPDATE [ConcordDb_V5_QA].dbo.RolePermission
SET Deleted = 1, Updated = @Now
WHERE UserRoleID = @QaRoleId AND Deleted = 0
  AND PermissionID NOT IN (SELECT QaPermissionId FROM @Desired);
DECLARE @Removed int = @@ROWCOUNT;

INSERT INTO [ConcordDb_V5_QA].dbo.RolePermission (UserRoleID, PermissionID, NetUID, Created, Updated, Deleted)
SELECT @QaRoleId, d.QaPermissionId, NEWID(), @Now, @Now, 0
FROM @Desired d
WHERE NOT EXISTS (
    SELECT 1 FROM [ConcordDb_V5_QA].dbo.RolePermission rp
    WHERE rp.UserRoleID = @QaRoleId AND rp.PermissionID = d.QaPermissionId AND rp.Deleted = 0);
DECLARE @Added int = @@ROWCOUNT;

DECLARE @DevVersion bigint, @DevUpdatedBy uniqueidentifier;
SELECT @DevVersion = Version, @DevUpdatedBy = UpdatedByUserNetUID
FROM [ConcordDb_V5].dbo.RoleEventPermissionRevision
WHERE UserRoleID = @DevRoleId AND Deleted = 0;

IF @DevVersion IS NOT NULL
BEGIN
    IF EXISTS (SELECT 1 FROM [ConcordDb_V5_QA].dbo.RoleEventPermissionRevision WHERE UserRoleID = @QaRoleId)
        UPDATE [ConcordDb_V5_QA].dbo.RoleEventPermissionRevision
        SET Version = @DevVersion, UpdatedByUserNetUID = @DevUpdatedBy, Deleted = 0, Updated = @Now
        WHERE UserRoleID = @QaRoleId
          AND (Version <> @DevVersion OR Deleted = 1 OR ISNULL(UpdatedByUserNetUID, '00000000-0000-0000-0000-000000000000') <> ISNULL(@DevUpdatedBy, '00000000-0000-0000-0000-000000000000'));
    ELSE
        INSERT INTO [ConcordDb_V5_QA].dbo.RoleEventPermissionRevision (UserRoleID, Version, UpdatedByUserNetUID, NetUID, Created, Updated, Deleted)
        VALUES (@QaRoleId, @DevVersion, @DevUpdatedBy, NEWID(), @Now, @Now, 0);
END;

IF NOT EXISTS (SELECT 1 FROM [ConcordDb_V5_QA].dbo.[User] WHERE NetUID = @UserNetUid)
    INSERT INTO [ConcordDb_V5_QA].dbo.[User]
        (Created, Deleted, Email, FirstName, LastName, MiddleName, NetUID, PhoneNumber, Region, Updated, UserRoleID, IsActive, Abbreviation)
    SELECT Created, 0, Email, FirstName, LastName, MiddleName, NetUID, PhoneNumber, Region, @Now, @QaRoleId, IsActive, Abbreviation
    FROM [ConcordDb_V5].dbo.[User] WHERE ID = @DevUserId;
ELSE
    UPDATE q
    SET Email = d.Email, FirstName = d.FirstName, LastName = d.LastName, MiddleName = d.MiddleName,
        PhoneNumber = d.PhoneNumber, Region = d.Region, UserRoleID = @QaRoleId, IsActive = d.IsActive,
        Abbreviation = d.Abbreviation, Deleted = 0, Updated = @Now
    FROM [ConcordDb_V5_QA].dbo.[User] q
    JOIN [ConcordDb_V5].dbo.[User] d ON d.NetUID = q.NetUID
    WHERE d.ID = @DevUserId
      AND (q.Deleted = 1 OR ISNULL(q.UserRoleID, 0) <> @QaRoleId OR q.IsActive <> d.IsActive
           OR ISNULL(q.Email, N'') <> ISNULL(d.Email, N'') OR ISNULL(q.PhoneNumber, N'') <> ISNULL(d.PhoneNumber, N''));

COMMIT TRANSACTION;

SELECT
    N'qa-mirror' AS step,
    @QaRoleId AS qa_role_id,
    (SELECT COUNT(*) FROM [ConcordDb_V5_QA].dbo.RolePermission WHERE UserRoleID = @QaRoleId AND Deleted = 0) AS qa_active_grants,
    (SELECT COUNT(*) FROM @Desired) AS dev_active_grants,
    @Added AS added,
    @Removed AS removed,
    (SELECT Version FROM [ConcordDb_V5_QA].dbo.RoleEventPermissionRevision WHERE UserRoleID = @QaRoleId) AS qa_revision,
    (SELECT COUNT(*) FROM [ConcordDb_V5_QA].dbo.[User] WHERE NetUID = @UserNetUid AND UserRoleID = @QaRoleId AND Deleted = 0) AS qa_user_rows;
