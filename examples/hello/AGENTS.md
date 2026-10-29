# hello

The smallest possible golem project: one static page served by nginx.

- **Stack:** `index.html` in `nginx:1.29-alpine` (`Dockerfile`).
- **Run locally:** `docker build -t hello . && docker run --rm -p 8080:80 hello`
- **Test:** the image build is the only check.
- **Deploy:** push to `main`; the `build` workflow pushes
  `ghcr.io/<org>/hello:<sha>`. Then ask the deployer for
  `{"slug": "hello", "sha": "<sha>"}`. `deploy/compose.yml` references the
  image untagged; the deployer pins it to the commit.
- **Gotchas:** none yet.
