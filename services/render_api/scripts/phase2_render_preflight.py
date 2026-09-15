"""Read-only Render preflight. Never prints credentials, URLs, source data or API bodies."""
import json
import ssl
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[3]

def main():
    cfg = dotenv_values(ROOT / '.env')
    if cfg.get('ENVIRONMENT') != 'test':
        raise ValueError('test_environment_required')
    base = cfg.get('RENDER_API_BASE_URL', '').rstrip('/')
    if base != 'https://api.render.com/v1':
        raise ValueError('render_api_origin_mismatch')
    owner = cfg.get('RENDER_WORKSPACE_ID', '')
    target = cfg.get('RENDER_BASE_URL', '').rstrip('/')
    parsed = urlsplit(target)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise ValueError('runtime_origin_invalid')
    with httpx.Client(timeout=30, follow_redirects=False, verify=ssl.create_default_context()) as client:
        headers = {'Authorization': 'Bearer ' + cfg.get('RENDER_API_KEY', '')}
        response = client.get(base + '/owners/' + owner, headers=headers)
        print('workspace_lookup_http:', response.status_code)
        if response.status_code != 200:
            return
        data = response.json()
        data = data.get('owner', data)
        if data.get('id') != owner or data.get('name') != 'Trident Wealth':
            raise ValueError('workspace_identity_mismatch')
        print('workspace_identity: verified Trident Wealth')
        matches = []
        cursor = None
        for _ in range(10):
            params = {'ownerId': owner, 'limit': 100}
            if cursor:
                params['cursor'] = cursor
            response = client.get(base + '/services', headers=headers, params=params)
            if response.status_code != 200:
                print('service_lookup_http:', response.status_code)
                return
            entries = response.json()
            for entry in entries:
                service = entry['service']
                if service.get('serviceDetails', {}).get('url', '').rstrip('/') == target:
                    matches.append(service)
            if len(entries) < 100:
                break
            cursor = entries[-1]['cursor']
        if len(matches) != 1 or matches[0].get('ownerId') != owner:
            raise ValueError('configured_service_not_uniquely_verified')
        service = matches[0]
        if 'leadgen' not in service.get('name', '').lower() or 'test' not in service.get('name', '').lower():
            raise ValueError('isolated_service_name_not_verified')
        print('configured_test_service: verified')
        for path in ('/health', '/dashboard/summary'):
            response = client.get(target + path, headers={'Authorization': 'Bearer ' + cfg.get('DASHBOARD_API_TOKEN', '')})
            print(path, 'http:', response.status_code)
            if response.status_code == 200 and path.endswith('summary'):
                data = response.json()
                print(json.dumps({key: data.get(key) for key in ('environment', 'paused', 'flags', 'raw_pending', 'candidates', 'validated', 'delivered')}))

if __name__ == '__main__':
    try:
        main()
    except httpx.HTTPError as exc:
        print('preflight_network_error')
        cause = exc
        while cause:
            print('exception_type:', type(cause).__name__)
            cause = cause.__cause__
        raise SystemExit(2) from None
    except (ValueError, KeyError, TypeError):
        print('preflight_configuration_or_identity_error')
        raise SystemExit(1) from None
