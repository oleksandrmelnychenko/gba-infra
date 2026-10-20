SET NOCOUNT ON;
SET XACT_ABORT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;

IF CONVERT(nvarchar(128), @@SERVERNAME) <> N'$(ServerIdentity)'
    THROW 54970, N'Refusing to run outside the approved DEV SQL instance.', 1;

DECLARE @UserNetUid uniqueidentifier = '$(UserNetUid)';
DECLARE @Login nvarchar(256) = N'$(Login)';
DECLARE @RoleName nvarchar(256) = N'$(RoleName)';

IF NOT EXISTS (
    SELECT 1
    FROM [ConcordIdentityDb].dbo.AspNetUsers u
    JOIN [ConcordIdentityDb].dbo.AspNetUserRoles ur ON ur.UserId = u.Id
    JOIN [ConcordIdentityDb].dbo.AspNetRoles r ON r.Id = ur.RoleId
    WHERE u.NetId = @UserNetUid AND u.UserName = @Login AND r.Name = @RoleName)
    THROW 54971, N'The verifier identity with the verifier role is missing on DEV.', 1;

UPDATE [ConcordIdentityDb].dbo.AspNetUsers
SET PasswordHash = N'$(PasswordHash)',
    SecurityStamp = UPPER(REPLACE(CONVERT(nvarchar(36), NEWID()), N'-', N'')),
    ConcurrencyStamp = CONVERT(nvarchar(36), NEWID()),
    LockoutEnd = NULL,
    AccessFailedCount = 0
WHERE NetId = @UserNetUid AND UserName = @Login;

IF @@ROWCOUNT <> 1
    THROW 54972, N'Expected to update exactly one verifier identity row.', 1;

SELECT N'password-set' AS step, 1 AS rows_updated;
