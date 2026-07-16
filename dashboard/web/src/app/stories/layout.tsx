// The dashboard locks body scrolling (fixed-viewport app); story pages
// scroll inside their own full-height container.

export default function StoriesLayout({ children }: { children: React.ReactNode }) {
  return <div className="h-[100dvh] overflow-y-auto">{children}</div>;
}
