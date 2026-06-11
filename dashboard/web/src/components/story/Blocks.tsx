"use client";

// VEDA-UI inspired layout primitives for MDX storymaps.

export function Block({ children }: { children: React.ReactNode }) {
  return <section className="my-10">{children}</section>;
}

export function Prose({ children }: { children: React.ReactNode }) {
  return <div className="story">{children}</div>;
}

export function Figure({ children }: { children: React.ReactNode }) {
  return <figure className="my-6">{children}</figure>;
}

export function Caption({ children }: { children: React.ReactNode }) {
  return (
    <figcaption className="text-[11px] mt-2 px-1" style={{ color: "var(--td)" }}>
      {children}
    </figcaption>
  );
}
