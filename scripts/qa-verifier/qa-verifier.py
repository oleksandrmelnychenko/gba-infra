#!/usr/bin/env python3
import argparse
import base64
import datetime
import hashlib
import json
import os
import pathlib
import re
import secrets
import struct
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
PERMISSIONS_FILE = HERE / 'permissions.json'
DEV_API = os.environ.get('GBA_DEV_API', 'http://127.0.0.1:35981').rstrip('/')
CONSOLE = os.environ.get('GBA_CONSOLE_ORIGIN', 'http://127.0.0.1:8083').rstrip('/')
OPERATOR_DIR = os.environ.get('GBA_OPERATOR_API_DIR', '/private/gba-dev-rehearsal-20261014')
SQL_CONTAINER = os.environ.get('SQL_CONTAINER', 'gba-dev-gba-mssql-1')
SERVER_IDENTITY = os.environ.get('SERVER_IDENTITY', '01934d77f334')
CREDENTIALS = pathlib.Path(os.environ.get('QA_VERIFIER_CREDENTIALS', '/private/qa-desk-verifier/credentials.private.json'))

LOGIN = '0990012742'
EMAIL = 'qa.verifier@gba.test'
FIRST_NAME = 'QA'
LAST_NAME = 'Verifier'
MIDDLE_NAME = 'Desk'

GUID = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')


def load_permissions():
    return json.loads(PERMISSIONS_FILE.read_text())


def operator_token():
    sys.path.insert(0, OPERATOR_DIR)
    import dev_api
    return dev_api.auth()


def api(method, path, body=None, query=None, token=None, base=None):
    url = (base or DEV_API) + '/api/v1/uk' + path
    if query:
        url += '?' + urllib.parse.urlencode(query)
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        raw = error.read()
        status = error.code
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = raw.decode(errors='replace')
    return status, payload


def unwrap(payload):
    if isinstance(payload, dict):
        for key in ('Body', 'body'):
            if key in payload:
                return payload[key]
    return payload


def require(status, payload, what):
    if status != 200:
        text = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
        raise SystemExit(f'{what} failed: HTTP {status}: {text[:600]}')
    return unwrap(payload)


def sql_literal(value):
    return str(value).replace("'", "''")


def run_sql(template_name, variables, tsv=True):
    text = (HERE / template_name).read_text()
    for name, value in variables.items():
        text = text.replace('$(' + name + ')', sql_literal(value))
    leftover = re.findall(r'\$\((\w+)\)', text)
    if leftover:
        raise SystemExit(f'{template_name}: unresolved variables {sorted(set(leftover))}')
    flags = '-S localhost -U sa -C -b -I -W -w 65535 -d master'
    if tsv:
        flags += ' -s "|"'
    command = ['docker', 'exec', '-i', SQL_CONTAINER, 'sh', '-lc',
               'SQLCMDPASSWORD="$MSSQL_SA_PASSWORD" exec /opt/mssql-tools18/bin/sqlcmd ' + flags + ' -i /dev/stdin']
    result = subprocess.run(command, input=text, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f'{template_name} failed:\n{result.stdout}\n{result.stderr}')
    return result.stdout


def sql_rows(query):
    command = ['docker', 'exec', '-i', SQL_CONTAINER, 'sh', '-lc',
               'SQLCMDPASSWORD="$MSSQL_SA_PASSWORD" exec /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -C -b -I -W -w 65535 -h -1 -s "|" -d master -i /dev/stdin']
    result = subprocess.run(command, input='SET NOCOUNT ON;\n' + query, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f'query failed:\n{result.stdout}\n{result.stderr}')
    return [line.split('|') for line in result.stdout.splitlines() if line.strip()]


def password_hash(password):
    salt = secrets.token_bytes(16)
    iterations = 100000
    subkey = hashlib.pbkdf2_hmac('sha512', password.encode(), salt, iterations, 32)
    blob = bytes([1]) + struct.pack('>III', 2, iterations, 16) + salt + subkey
    return base64.b64encode(blob).decode()


def desired_keys(catalog, reviewed):
    active = {item['key'] for item in catalog['permissions'] if item.get('active')}
    granted = {item['key'] for item in reviewed['grant']}
    withheld = {item['key'] for item in reviewed['withhold']}
    unreviewed = sorted(active - granted - withheld)
    keys = [item['key'] for item in catalog['permissions'] if item.get('active') and item['key'] in granted]
    return keys, unreviewed, sorted(granted - active)


def find_role(token, name):
    roles = require(*api('GET', '/usermanagement/profiles/roles/registry', token=token), 'role registry')
    matches = [role for role in roles or [] if role.get('Name') == name and not role.get('Deleted')]
    if len(matches) > 1:
        raise SystemExit(f'more than one role is named {name}')
    return matches[0] if matches else None


def dev_user_row():
    rows = sql_rows(
        "SELECT CONVERT(nvarchar(36), u.NetUID), CONVERT(nvarchar(36), r.NetUID), r.Name "
        "FROM [ConcordDb_V5].dbo.[User] u LEFT JOIN [ConcordDb_V5].dbo.UserRole r ON r.ID = u.UserRoleID "
        f"WHERE u.Deleted = 0 AND u.Email = N'{sql_literal(EMAIL)}' AND u.PhoneNumber = N'{sql_literal(LOGIN)}';")
    if len(rows) > 1:
        raise SystemExit('more than one verifier user row on DEV')
    return [rows[0][0].lower(), rows[0][1].lower(), rows[0][2]] if rows else None


def write_credentials(payload):
    CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(CREDENTIALS.parent, 0o700)
    temporary = CREDENTIALS.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    os.chmod(temporary, 0o600)
    os.replace(temporary, CREDENTIALS)


def read_credentials():
    if not CREDENTIALS.exists():
        return None
    return json.loads(CREDENTIALS.read_text())


def console_login(login, password):
    status, payload = api('POST', '/usermanagement/token', body={'Username': login, 'Password': password}, base=CONSOLE)
    if status != 200:
        raise SystemExit(f'verifier login through the console origin failed: HTTP {status}')
    body = unwrap(payload) or {}
    token = body.get('AccessToken') or (payload or {}).get('AccessToken')
    if not token:
        raise SystemExit('verifier login returned no access token')
    return token


def provision_dev(args):
    reviewed = load_permissions()
    role_spec = reviewed['role']
    token = operator_token()
    catalog = require(*api('GET', '/permissions/catalog', token=token), 'permission catalog')
    keys, unreviewed, retired = desired_keys(catalog, reviewed)
    if unreviewed:
        print(f'warning: {len(unreviewed)} catalog keys are not reviewed and stay withheld: {", ".join(unreviewed)}')
    if retired:
        print(f'note: {len(retired)} reviewed grant keys are no longer active: {", ".join(retired)}')

    role = find_role(token, role_spec['name'])
    if role is None:
        created = require(*api('POST', '/usermanagement/profiles/roles/create', token=token, body={
            'Name': role_spec['name'],
            'Dashboard': role_spec['dashboard'],
            'UserRoleType': role_spec['userRoleType'],
        }), 'role create')
        role = find_role(token, role_spec['name'])
        if role is None:
            raise SystemExit(f'role create returned {created!r} but the role is not listed')
        print(f"role created: {role['Name']} {role['NetUid']}")
    else:
        print(f"role present: {role['Name']} {role['NetUid']}")
    if role.get('UserRoleType') != role_spec['userRoleType']:
        raise SystemExit(f"role type is {role.get('UserRoleType')}, expected {role_spec['userRoleType']}")
    role_net_uid = role['NetUid']

    current = require(*api('GET', f'/usermanagement/profiles/roles/{role_net_uid}/event-permissions', token=token), 'role permissions')
    current_keys = set(current.get('permissionKeys') or [])
    if current_keys != set(keys):
        updated = require(*api('PUT', f'/usermanagement/profiles/roles/{role_net_uid}/event-permissions', token=token, body={
            'expectedVersion': current.get('version', 0),
            'permissionKeys': keys,
        }), 'role permissions update')
        print(f"grants updated: version {updated.get('version')} keys {len(updated.get('permissionKeys') or [])} "
              f"(+{len(set(keys) - current_keys)} -{len(current_keys - set(keys))})")
    else:
        print(f"grants unchanged: version {current.get('version')} keys {len(current_keys)}")

    existing = dev_user_row()
    credentials = read_credentials()
    if existing and existing[1].lower() != role_net_uid.lower():
        raise SystemExit(f'verifier user exists with role {existing[2]}; refusing to continue')
    need_password = args.rotate_password or credentials is None or existing is None
    if existing is None:
        status, payload = api('POST', '/usermanagement/users/create', token=token,
                              query={'password': secrets.token_urlsafe(24)},
                              body={
                                  'FirstName': FIRST_NAME,
                                  'LastName': LAST_NAME,
                                  'MiddleName': MIDDLE_NAME,
                                  'Email': EMAIL,
                                  'PhoneNumber': LOGIN,
                                  'Region': 'uk',
                                  'UserRole': {'NetUid': role_net_uid},
                              })
        result = require(status, payload, 'user create')
        if isinstance(result, dict) and result.get('Succeeded') is False:
            raise SystemExit(f'user create refused: {json.dumps(result, ensure_ascii=False)[:600]}')
        existing = dev_user_row()
        if existing is None:
            raise SystemExit('user create succeeded but the user row is missing')
        print(f'user created: {LOGIN} {existing[0]}')
    else:
        print(f'user present: {LOGIN} {existing[0]}')
    user_net_uid = existing[0]

    if need_password:
        password = secrets.token_urlsafe(30)
        run_sql('set-password.sql', {
            'ServerIdentity': SERVER_IDENTITY,
            'UserNetUid': user_net_uid,
            'Login': LOGIN,
            'RoleName': role_spec['name'],
            'PasswordHash': password_hash(password),
        })
        credentials = {
            'Login': LOGIN,
            'Password': password,
            'Email': EMAIL,
            'UserNetUid': user_net_uid,
            'Role': role_spec['name'],
            'RoleNetUid': role_net_uid,
            'UserRoleType': role_spec['userRoleType'],
            'Environment': 'gba-dev ConcordDb_V5 + ConcordIdentityDb, mirrored to ConcordDb_V5_QA',
            'ConsoleUrl': 'https://gba-console-dev.85.17.167.167.nip.io',
            'ConsoleLocalUrl': 'http://127.0.0.1:8083',
            'LoginEndpoint': '/api/v1/uk/usermanagement/token',
            'Purpose': 'QA Desk read-only verification (view/open/read permissions only)',
            'PermissionsFile': str(PERMISSIONS_FILE),
            'Created': datetime.date.today().isoformat(),
        }
        write_credentials(credentials)
        print(f'password set; credentials written to {CREDENTIALS}')
    elif credentials.get('UserNetUid') != user_net_uid or credentials.get('RoleNetUid') != role_net_uid:
        credentials.update({'UserNetUid': user_net_uid, 'RoleNetUid': role_net_uid})
        write_credentials(credentials)

    verifier_token = console_login(credentials['Login'], credentials['Password'])
    mine = require(*api('GET', '/permissions/me', token=verifier_token, base=CONSOLE), 'verifier permissions')
    granted = set(mine.get('permissionKeys') or [])
    if granted != set(keys):
        raise SystemExit(f'verifier holds {len(granted)} keys, expected {len(keys)}')
    print(f'verifier login ok through {CONSOLE}; holds {len(granted)} keys, catalog {mine.get("catalogVersion")}')
    return role_net_uid, user_net_uid


def provision_qa(role_net_uid=None, user_net_uid=None):
    if role_net_uid is None or user_net_uid is None:
        credentials = read_credentials()
        if credentials is None:
            raise SystemExit('no credentials file; provision DEV first')
        role_net_uid, user_net_uid = credentials['RoleNetUid'], credentials['UserNetUid']
    for value in (role_net_uid, user_net_uid):
        if not GUID.match(value):
            raise SystemExit(f'not a GUID: {value}')
    output = run_sql('qa-mirror.sql', {
        'ServerIdentity': SERVER_IDENTITY,
        'RoleNetUid': role_net_uid,
        'UserNetUid': user_net_uid,
        'RoleName': load_permissions()['role']['name'],
    })
    print(output.strip())


def remove(args):
    role_spec = load_permissions()['role']
    targets = []
    if args.env in ('dev', 'all'):
        targets.append(('ConcordDb_V5', 'ConcordIdentityDb'))
    if args.env in ('qa', 'all'):
        targets.append(('ConcordDb_V5_QA', 'ConcordIdentityDb_QA'))
    for v5, identity in targets:
        output = run_sql('remove.sql', {
            'ServerIdentity': SERVER_IDENTITY,
            'V5Db': v5,
            'IdentityDb': identity,
            'RoleName': role_spec['name'],
            'UserRoleType': role_spec['userRoleType'],
            'Login': LOGIN,
            'Email': EMAIL,
            'Commit': 0 if args.dry_run else 1,
        })
        print(output.strip())
    if not args.dry_run and args.env in ('dev', 'all') and CREDENTIALS.exists() and not args.keep_credentials:
        CREDENTIALS.unlink()
        print(f'removed {CREDENTIALS}')


def status(_args):
    role_name = sql_literal(load_permissions()['role']['name'])
    for v5, identity in (('ConcordDb_V5', 'ConcordIdentityDb'), ('ConcordDb_V5_QA', 'ConcordIdentityDb_QA')):
        rows = sql_rows(
            f"SELECT N'{v5}', "
            f"(SELECT COUNT(*) FROM [{v5}].dbo.UserRole WHERE Name = N'{role_name}' AND Deleted = 0), "
            f"(SELECT COUNT(*) FROM [{v5}].dbo.RolePermission rp JOIN [{v5}].dbo.UserRole r ON r.ID = rp.UserRoleID "
            f"JOIN [{v5}].dbo.Permission p ON p.ID = rp.PermissionID AND p.Deleted = 0 AND p.Kind = N'Event' "
            f"WHERE r.Name = N'{role_name}' AND rp.Deleted = 0), "
            f"(SELECT COUNT(*) FROM [{v5}].dbo.[User] WHERE Email = N'{sql_literal(EMAIL)}' AND PhoneNumber = N'{sql_literal(LOGIN)}' AND Deleted = 0), "
            f"(SELECT COUNT(*) FROM [{identity}].dbo.AspNetUsers WHERE UserName = N'{sql_literal(LOGIN)}');")
        for row in rows:
            print(f'{row[0]}: role={row[1]} grants={row[2]} user={row[3]} identity={row[4]}')
    print(f'credentials: {CREDENTIALS} {"present" if CREDENTIALS.exists() else "absent"}')


PRODUCT = '47b16aa5-fc36-4b56-ae75-17b607f4afcf'
CLIENT = '421edbe0-9687-4d4b-af2e-b7b6e5a6628a'
EMPTY = '00000000-0000-0000-0000-000000000000'
MALFORMED = b'{"verifierProbe": '


def read_checks(today, week_ago, month_ago):
    return [
        ('sales', f'/sales/ukraine/registry?fastEcommerce=false&forEcommerce=false&from={week_ago}&fromShipments=false&includeDetails=false&limit=20&offset=0&status=All&to={today}&type=All'),
        ('sales', '/clients/sales-ukraine/registry/search?filterSql=RegionCode.Value/Client.FullName&limit=50&offset=0&value=KD02801'),
        ('sales', '/sales/geography?metric=sales&period=all'),
        ('sales', '/sales/cockpit/head/dashboard'),
        ('clients', '/clients/registry/all/filtered?filterSql=RegionCode.Value/Client.FullName/Client.USREOU&limit=20&offset=0&typeRoleFilter=1&value=KD02801'),
        ('clients', f'/clients/structure/details?netId={CLIENT}'),
        ('clients', '/clients/suppliers/registry/all/filtered?filterSql=RegionCode.Value/Client.FullName&limit=20&offset=0&value='),
        ('products', f'/products/assortment/details?netId={PRODUCT}'),
        ('products', f'/products/assortment/siblings?productNetId={PRODUCT}'),
        ('products', '/products/intelligence/assortment/overview'),
        ('consignments', f'/consignments/info/assortment/income/filtered?from=2000-01-01&productNetId={PRODUCT}&to={today}'),
        ('consignments', f'/consignments/info/assortment/outcome/filtered?from={month_ago}&productNetId={PRODUCT}&to={today}'),
        ('warehouse', f'/sales/warehouse-ukraine/invoices/registry?status=Packaging&type=All&value=&from={week_ago}&to={today}&fromShipments=true&limit=20&offset=0&forEcommerce=false&includeDetails=false'),
        ('warehouse', '/storages/warehouse-accounting/all'),
        ('warehouse', f'/products/transfers/page/registry?from={week_ago}&limit=20&offset=0&to={today}'),
        ('warehouse', f'/orders/depreciated/write-off/registry?from={week_ago}&limit=20&offset=0&to={today}'),
        ('orders', f'/supplies/ukraine/order/all/filtered?from={week_ago}&limit=20&offset=0&supplierName=&to={today}'),
        ('orders', f'/delivery/product/protocol/registry/all?limit=20&offset=0&from={month_ago}&to={today}'),
        ('accounting', '/payments/registers/accounting/all'),
        ('accounting', '/payments/movements/accounting/all'),
        ('accounting', '/payments/registers/income-cashflows/registry/search?value='),
        ('accounting', f'/payments/tasks/grouped/available-payments/all/filtered?from={month_ago}&limit=11&offset=0&to={today}&typePaymentTask=2'),
        ('reports', '/report/datasets'),
        ('reports', '/report/templates'),
        ('administration', '/usermanagement/profiles/users/registry'),
        ('administration', '/permissions/catalog?kind=event'),
    ]


GATED_READS = [
    ('sales.ukraine.sale.create', '/clients/sales-ukraine/create/search?filterSql=RegionCode.Value/Client.FullName&limit=20&offset=0&value=KD02801'),
    ('payments.available_payments.outcome_order.create', '/payments/registers/search?value='),
    ('warehouse_accounting.transfers.transfer.create', '/storages/get/all'),
    ('administration.online_shop_seo.synchronization.run', '/data/sync/status'),
]


def mutation_checks(role_net_uid):
    return [
        ('create', 'administration.roles.role.create', 'POST', '/usermanagement/profiles/roles/create', MALFORMED),
        ('update', 'administration.roles.event_permissions.edit', 'PUT', f'/usermanagement/profiles/roles/{role_net_uid}/event-permissions',
         json.dumps({'expectedVersion': -1, 'permissionKeys': ['administration.roles.event_permissions.edit']}).encode()),
        ('create', 'administration.users.user.create', 'POST', '/usermanagement/users/create', MALFORMED),
        ('create', 'sales.ukraine.sale.create', 'POST', '/sales/ukraine/create', MALFORMED),
        ('update', 'sales.ukraine.sale.edit', 'POST', '/sales/ukraine/edit', MALFORMED),
        ('update', 'sales.ukraine.sale.unlock', 'PATCH', f'/sales/unlock?netId={EMPTY}', None),
        ('create', 'counterparties.clients.client.create', 'POST', '/clients/registry/new', MALFORMED),
        ('delete', 'counterparties.clients.client.delete', 'DELETE', f'/clients/registry/delete?netId={EMPTY}', None),
        ('update', 'products.assortment.product.edit', 'POST', '/products/assortment/update', MALFORMED),
        ('create', 'warehouse_accounting.transfers.transfer.create', 'POST', '/products/transfers/new', MALFORMED),
        ('approve', 'warehouses.ukraine.shipment.carry_out', 'POST', '/sales/shipments/warehouse-ukraine/carry-out', MALFORMED),
        ('approve', 'orders.ukraine.logistic_way.approve_order', 'POST', '/supplies/orders/direct-supply-order/logistic-way/approve', MALFORMED),
        ('import', 'products.placements.file.import', 'POST', '/products/placements/storage/page/import/file', MALFORMED),
        ('payment', 'accounting.income_cashflows.client_payment.create', 'POST', '/payments/orders/income/accounting/create/client-payment', MALFORMED),
        ('payment', 'payments.payment_accounts.transfer.create', 'POST', '/payments/registers/transfers/new', MALFORMED),
        ('payment', 'services.consumable_orders.order.pay', 'POST', '/payments/orders/outcome/consumable-orders/pay', MALFORMED),
        ('create', 'role InternalMutationUser', 'POST', '/exchangerates/new', MALFORMED),
    ]


def raw_call(method, url, token, body=None, cookies=None):
    headers = {'Accept': 'application/json', 'Authorization': 'Bearer ' + token}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    if cookies:
        headers['Cookie'] = cookies
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            response.read()
            return response.status
    except urllib.error.HTTPError as error:
        error.read()
        return error.code
    except (urllib.error.URLError, TimeoutError) as error:
        return f'error: {error}'


def prove(args):
    credentials = read_credentials()
    if credentials is None:
        raise SystemExit('no credentials file; provision first')
    token = console_login(credentials['Login'], credentials['Password'])
    today = datetime.date.today()
    week_ago = (today - datetime.timedelta(days=7)).isoformat()
    month_ago = (today - datetime.timedelta(days=30)).isoformat()
    targets = []
    if args.env in ('dev', 'all'):
        targets.append(('dev', CONSOLE, None))
    if args.env in ('qa', 'all'):
        targets.append(('qa', CONSOLE, 'gba_env=qa'))
        targets.append(('qa-direct', os.environ.get('GBA_QA_API', 'http://127.0.0.1:35996'), None))
    report = {'login': credentials['Login'], 'userNetUid': credentials['UserNetUid'], 'startedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'results': []}
    failures = 0
    for name, base, cookies in targets:
        for module, path in read_checks(today.isoformat(), week_ago, month_ago):
            if name == 'qa-direct' and path.startswith('/report/'):
                continue
            status = raw_call('GET', base + '/api/v1/uk' + path, token, cookies=cookies)
            ok = status == 200
            failures += 0 if ok else 1
            report['results'].append({'target': name, 'group': 'read', 'module': module, 'method': 'GET', 'path': path.split('?')[0], 'expected': 200, 'status': status, 'ok': ok})
        for key, path in GATED_READS:
            status = raw_call('GET', base + '/api/v1/uk' + path, token, cookies=cookies)
            ok = status == 403
            failures += 0 if ok else 1
            report['results'].append({'target': name, 'group': 'read gated by a withheld key', 'module': key, 'method': 'GET', 'path': path.split('?')[0], 'expected': 403, 'status': status, 'ok': ok})
        for action, key, method, path, body in mutation_checks(credentials['RoleNetUid']):
            status = raw_call(method, base + '/api/v1/uk' + path, token, body=body, cookies=cookies)
            ok = status == 403
            failures += 0 if ok else 1
            report['results'].append({'target': name, 'group': f'mutation:{action}', 'module': key, 'method': method, 'path': path.split('?')[0], 'expected': 403, 'status': status, 'ok': ok})
    report['failures'] = failures
    for row in report['results']:
        print(f"{'ok  ' if row['ok'] else 'FAIL'} {row['target']:<9} {row['group']:<30} {row['method']:<6} {row['status']!s:<4} {row['path']}  [{row['module']}]")
    print(f"{len(report['results']) - failures}/{len(report['results'])} checks as expected")
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(f'report written to {args.out}')
    if failures:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description='Provision or remove the read-only QA Verifier identity on DEV and QA.')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('provision')
    p.add_argument('--env', choices=('dev', 'qa', 'all'), default='all')
    p.add_argument('--rotate-password', action='store_true')
    r = sub.add_parser('remove')
    r.add_argument('--env', choices=('dev', 'qa', 'all'), default='all')
    r.add_argument('--dry-run', action='store_true')
    r.add_argument('--keep-credentials', action='store_true')
    sub.add_parser('status')
    v = sub.add_parser('prove')
    v.add_argument('--env', choices=('dev', 'qa', 'all'), default='all')
    v.add_argument('--out')
    args = parser.parse_args()
    if args.command == 'provision':
        role_net_uid = user_net_uid = None
        if args.env in ('dev', 'all'):
            role_net_uid, user_net_uid = provision_dev(args)
        if args.env in ('qa', 'all'):
            provision_qa(role_net_uid, user_net_uid)
    elif args.command == 'remove':
        remove(args)
    elif args.command == 'prove':
        prove(args)
    else:
        status(args)


if __name__ == '__main__':
    main()
