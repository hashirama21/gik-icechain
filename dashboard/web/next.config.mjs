import nextMDX from "@next/mdx";

const withMDX = nextMDX({
  extension: /\.mdx?$/,
  options: { remarkPlugins: [], rehypePlugins: [] },
});

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export → deployable on GitHub Pages (zero-cost, no Node server).
  output: "export",
  // GitHub Pages serves the project under /gik-icechain/ ; override with env.
  basePath: process.env.NEXT_PUBLIC_BASE_PATH || "",
  images: { unoptimized: true },
  pageExtensions: ["js", "jsx", "ts", "tsx", "md", "mdx"],
  trailingSlash: true,
};

export default withMDX(nextConfig);
