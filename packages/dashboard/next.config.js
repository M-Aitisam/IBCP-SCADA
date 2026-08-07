/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: 'export', // static export — the dashboard has no server-side Next.js routes, the API lives in packages/backend
  images: {
    unoptimized: true, // required for static export
  },
  trailingSlash: true,
}

module.exports = nextConfig