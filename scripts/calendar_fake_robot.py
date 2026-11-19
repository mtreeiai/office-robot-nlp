"""Read calendar evidence from a different machine; never commands robot movement."""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None  # Never forward a robot credential to a redirected origin.


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',required=True)
    parser.add_argument('--room',choices=[f'{i:02d}' for i in range(1,8)],default='02')
    parser.add_argument('--arrival-start',help='ISO-8601 with offset; absent means PRELIMINARY')
    parser.add_argument('--duration-seconds',type=int,default=180)
    parser.add_argument('--known-version',type=int)
    args=parser.parse_args()
    origin=urlsplit(args.base_url)
    if (origin.scheme not in {'http','https'} or not origin.hostname or origin.username or origin.password
            or origin.query or origin.fragment or origin.path not in {'','/'}):
        raise SystemExit('Use an HTTPS server origin, or a loopback HTTP origin for local tests.')
    if origin.scheme=='http' and origin.hostname not in {'127.0.0.1','localhost','::1'}:
        raise SystemExit('Remote robot credentials require HTTPS.')
    token=os.environ.get('CALENDAR_ROBOT_TOKEN','')
    if len(token)<32: raise SystemExit('Set the distinct CALENDAR_ROBOT_TOKEN privately.')
    body={'roomId':args.room,'operation':'DOCUMENT_DELIVERY'}
    if args.arrival_start:
        try:
            start=datetime.fromisoformat(args.arrival_start.replace('Z','+00:00'))
            if start.tzinfo is None or not 1<=args.duration_seconds<=28800: raise ValueError()
            body['arrivalWindow']={'start':start.isoformat(),'end':(start+timedelta(seconds=args.duration_seconds)).isoformat()}
        except ValueError: raise SystemExit('Use an offset datetime and duration of 1–28800 seconds.') from None
    if args.known_version is not None: body['knownCalendarVersion']=args.known_version
    request=Request(args.base_url.rstrip('/')+'/v1/robot/availability/check',data=json.dumps(body).encode(),
                    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
    try:
        with build_opener(NoRedirect()).open(request,timeout=10) as response: evidence=json.load(response)
    except HTTPError as error:
        raise SystemExit(f'Calendar HTTP {error.code}; no permission inferred. Check credentials/configuration privately.') from None
    except (URLError,TimeoutError,ValueError):
        raise SystemExit('Calendar could not be checked; treat result as UNKNOWN.') from None
    print(json.dumps(evidence,ensure_ascii=False,indent=2))
    print('Calendar evidence only. Recheck expiry/version and local execution requirements; no motion command was sent.')


if __name__=='__main__': main()
