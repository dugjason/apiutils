#!/usr/bin/env python
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301 USA
#
# Copyright 2015 Michael Pitidis

"""
Retrieve posts, likes, or comments for Facebook objects through the Graph API.
"""

import requests
import sys, os, json, collections, argparse


GRAPH_URL = 'https://graph.facebook.com/v2.2'

def main(args):
    opts = parse_cli(args[1:], default_endpoints().keys(), GRAPH_URL)
    return process(opts, default_endpoints(), default_parameters())

def default_endpoints():
    return dict(
        feed = paginate_dates,
        posts = paginate_dates,
        likes = paginate_cursors,
        comments = paginate_cursors,
     )

def default_parameters():
    return dict(
        feed = dict(since='-1day'),
        posts = dict(since='-7days'),
        likes = dict(summary=1),
        comments = dict(summary=1, filter='stream'),
     )


def process(opts, endpoints, parameters):
    if not os.path.isdir(opts.destination):
        os.makedirs(opts.destination)

    objects = one_of(opts.objects, opts.objects_file)
    tokens = one_of(opts.tokens, opts.tokens_file)

    params = parameters[opts.endpoint]
    paginator = endpoints[opts.endpoint]

    if opts.query_parameters:
        params.update(opts.query_parameters)
    params['limit'] = opts.limit

    ramp_up = geometric_ramp_up(opts.limit_factor, opts.limit_max)
    progress = write_flush if opts.verbose else lambda x: x
    serialize = choose_serializer(opts.type)
    fmt = dict(endpoint=opts.endpoint, type=opts.type.replace('_pretty', ''))

    for obj in objects:
        url = '%s/%s/%s' % (opts.url.rstrip('/'), obj, opts.endpoint)
        count = 0
        for i, entry in enumerate(paginator(url, params, tokens, ramp_up), 1):
            fmt.update(dict(object=obj, i=i))
            write_file(os.path.join(opts.destination, opts.format % fmt), serialize(entry), opts.overwrite)
            count += len(entry['content'].get('data', []))
            progress("\r%s %d" % (obj, count))
        progress('\n')

    return 0

def choose_serializer(t):
    if t == 'yaml':
        try:
            import yaml
            return lambda x: yaml.safe_dump(x, default_flow_style=False, encoding='utf8', allow_unicode=True, width=1024**3)
        except ImportError:
            sys.stderr.write("python-yaml not available, using json format\n")
    if t == 'json_pretty':
        return lambda x: json.dumps(x, indent=2)
    return json.dumps

def paginate_cursors(endpoint, parameters, tokens, ramp_up = lambda x: x):
    return paginate(endpoint, parameters, tokens, 'after', extract_after, ramp_up)

def paginate_dates(endpoint, parameters, tokens, ramp_up = lambda x: x):
    return paginate(endpoint, parameters, tokens, 'until', extract_until, ramp_up)

def paginate(endpoint, parameters, tokens, cursor_key, cursor_extractor, ramp_up = lambda x: x):
    """Paginate through a graph endpoint using cursors."""
    params = dict(parameters)
    queue = collections.deque(tokens)
    while True:
        params['access_token'] = queue.popleft()
        response = requests.get(endpoint, params=params)
        content = parse_response(response)

        yield dict(content=content, status=response.status_code, endpoint=endpoint, parameters=params)

        cursor = cursor_extractor(content)
        data = content.get('data', [])
        if data and cursor and cursor != params.get(cursor_key):
            queue.append(params['access_token'])
            params[cursor_key] = cursor
            if 'limit' in params:
                params['limit'] = ramp_up(params['limit'])
        else:
            break

def extract_after(c):
    return c.get('paging', {}).get('cursors', {}).get('after')

def extract_until(c):
    return extract_query(c.get('paging', {}).get('next', '')).get('until')

def extract_query(s):
    parts = s.split('?', 1)
    if len(parts) == 2:
        return dict(p.split('=', 1) for p in parts[1].split('&'))
    return dict()

def one_of(*lists):
    return tuple(e.strip() for l in lists if l is not None for e in l)

def write_flush(s):
    sys.stderr.write(s)
    sys.stderr.flush()

def write_file(filename, data, overwrite=False):
    assert overwrite or not os.path.exists(filename) # XXX: race condition
    with open(filename, 'wt') as fd:
        fd.write(data)

def geometric_ramp_up(multiplier, ceiling):
    return lambda x: min(ceiling, x * multiplier)

def parse_response(r):
    try:
        return r.json()
    except:
        return dict(error=r.text)

def parse_cli(args, endpoints, graph_url):
    parser = argparse.ArgumentParser(
        description='Paginate through the Facebook Graph API',
        epilog='')
    add = parser.add_argument

    out = parser.add_argument_group('output').add_argument
    out('-d', '--destination', default='.', metavar='DIRECTORY',
        help='set output directory [%(default)s]')
    out('--type', choices=('json', 'json_pretty', 'yaml'), default='json',
        help='set output file type [%(default)s]')
    out('--format', default='%(endpoint)s-%(object)s-%(i)04d.%(type)s',
        help='set output file format [%(default)s]')
    out('--overwrite', action='store_true',
        help='overwrite output files')
    out('-v', '--verbose', action='store_true',
        help='print progress information on standard error')

    req = parser.add_argument_group('requests').add_argument
    req('-u', '--url', default=graph_url,
        help='set facebook graph base url [%(default)s]')
    req('-q', '--query-parameters', nargs=2, action='append', metavar=('KEY', 'VALUE'),
        help='specify additional query parameters, e.g. -q fields id,message')

    limits = parser.add_argument_group('limits').add_argument
    limits('-l', '--limit', type=int, default=25,
        help='set the initial request limit [%(default)s]')
    limits('--limit-max', type=int, default=3000,
        help='set the maximum request limit [%(default)s]')
    limits('--limit-factor', type=int, default=2,
        help='set limit multiplication factor [%(default)s]')

    tokens = parser.add_argument_group('tokens').add_argument
    t1 = tokens('-t', '--tokens', nargs='+', metavar='TOKEN',
        help='provide a pool of access tokens for performing requests')
    t2 = tokens('--tokens-file', type=argparse.FileType('rt'), metavar='FILENAME',
        help='read access tokens from a file one per line')

    objects = parser.add_argument_group('targets').add_argument
    objects('-e', '--endpoint', choices=endpoints, required=True,
            help='choose request endpoint')
    o1 = objects('objects', metavar='ID', nargs='*',
        help='facebook object IDs to retrieve data for')
    o2 = objects('--objects-file', type=argparse.FileType('rt'), metavar='FILENAME',
        help='read object IDs from a file one per line')

    opts = parser.parse_args(args)
    if not (opts.objects or opts.objects_file):
        parser.error("at least one %s or %s is required" % (o1.metavar, '/'.join(o2.option_strings)))
    if not (opts.tokens or opts.tokens_file):
        parser.error("at least one of %s or %s is required" % ('/'.join(t1.option_strings), '/'.join(t2.option_strings)))
    return opts


if __name__ == '__main__':
    sys.exit(main(sys.argv))

