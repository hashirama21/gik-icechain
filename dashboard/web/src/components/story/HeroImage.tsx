"use client";

// Satellite backdrop for the storymap hero. Decorative enhancement over the
// hero band's committed dark ground: if the NASA snapshot fails to load, the
// component removes itself and the band's gradient remains.

import { useState } from "react";
import { gibsHeroUrl } from "@/lib/gibs";

export default function HeroImage({ date }: { date: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return (
    <img
      src={gibsHeroUrl(date)}
      alt=""
      aria-hidden="true"
      loading="eager"
      decoding="async"
      className="absolute inset-0 w-full h-full object-cover"
      style={{ opacity: 0.85 }}
      onError={() => setFailed(true)}
    />
  );
}
