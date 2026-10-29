# golem behind a reverse proxy you already run

On a box where nothing else serves websites, skip this: golem takes ports 80 and
443 and gets its own certificates ([runbook](runbook.md)). This page is for a box,
or a network, where a reverse proxy of yours (Caddy, nginx, Traefik...) already
holds 80 and 443. golem then serves plain HTTP on a port of your choosing, and your
proxy does HTTPS and forwards golem's hosts to it.

## 1. Pick where golem listens

Set `EDGE_PORT` in `.env`, then run `bin/bootstrap.sh server` (it says which way
it's set up: "front door: ..."). Your proxy forwards to that address.

| Your proxy runs | `EDGE_PORT` | The proxy forwards to |
| --- | --- | --- |
| On this box, installed directly | `127.0.0.1:8800` | `http://127.0.0.1:8800` |
| On this box, in Docker | `127.0.0.1:8800` | `http://golem-traefik`, after joining the `golem-edge` network |
| On another machine on the LAN | `<this box's LAN address>:8800` | `http://<this box's LAN address>:8800` |

Only your proxy should reach `EDGE_PORT`: anything that reaches it skips HTTPS. Give
it an address, not just a port, on a box with a public IP. Docker publishes ports
past host firewalls like ufw, so a bare `8800` is open to the internet there. Don't
forward it on your router.

**A proxy in Docker** joins `golem-edge`, which golem creates and which has nothing
on it but golem's Traefik. Start golem first, then in the proxy's compose file:

```yaml
services:
  proxy:
    networks: [golem-edge]   # plus whatever it's on already
networks:
  golem-edge:
    external: true
```

## 2. What the proxy has to do

- **Forward every host under your domain:** `golem.<domain>`, `projects.<domain>`,
  and `*.<domain>`. golem gives each project its own host when it first deploys, so
  a list of names won't keep up: forward the wildcard.
- **Keep the `Host` header** as the visitor sent it. golem's Traefik routes by it.
- **Handle HTTPS** with a certificate that covers `*.<domain>`. Wildcard
  certificates need Let's Encrypt's DNS challenge: your proxy needs an API token for
  your DNS provider.
- **Say it was HTTPS:** set `X-Forwarded-Proto: https` (Caddy and Traefik do this
  without being asked). golem's Traefik passes it on, so sites don't redirect in
  circles.
- **Pass websockets through**, for projects that use them.

DNS: the wildcard record `*.<domain>` points at the public IP that reaches your
proxy, not necessarily this box.

## Examples

`example.com` is your `BASE_DOMAIN`; golem listens on `127.0.0.1:8800`.

**Caddy** (built with your DNS provider's module, e.g. `caddy-dns/cloudflare`):

```
*.example.com {
	tls {
		dns cloudflare {env.CF_API_TOKEN}
	}
	reverse_proxy 127.0.0.1:8800
}
```

Hosts you serve yourself under the same domain get their own blocks: Caddy picks
the most specific one.

**nginx** (a wildcard certificate from certbot's DNS plugin, say):

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 443 ssl;
    server_name *.example.com;
    ssl_certificate     /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8800;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }
}
```

**Traefik in Docker** (with a DNS-challenge resolver called `letsencrypt`): join
`golem-edge` as above, add a file provider to its static config
(`providers.file.directory`), and put this in that directory:

```yaml
http:
  routers:
    golem:
      rule: HostRegexp(`^.+\.example\.com$`)
      priority: 1  # your own routers for hosts under example.com win
      entryPoints: [websecure]
      service: golem
      tls:
        certResolver: letsencrypt
        domains:
          - main: example.com
            sans: ["*.example.com"]
  services:
    golem:
      loadBalancer:
        servers:
          - url: http://golem-traefik
```

golem labels every project container `traefik.enable=false`, so a Traefik that
reads Docker labels never picks them up by itself.

## Checking it

```sh
curl -sS -o /dev/null -w '%{http_code}\n' -H 'Host: golem.example.com' http://127.0.0.1:8800/   # 200: golem answers
curl -sS -o /dev/null -w '%{http_code}\n' https://golem.example.com/                            # 200: through your proxy
curl -sS -o /dev/null -w '%{http_code}\n' https://nothing-here.example.com/                     # 404: forwarded, no project
```

A 502 or a timeout from your proxy means it can't reach `EDGE_PORT`: check the
address in the table above. A certificate error on `nothing-here` means the proxy's
certificate doesn't cover the wildcard.

## Switching

To let golem take 80 and 443 itself again, stop your proxy, empty `EDGE_PORT`, run
`bin/bootstrap.sh server`, then `docker compose up -d`. It works the other way round
too: set `EDGE_PORT`, bootstrap, `docker compose up -d`, then start your proxy.
