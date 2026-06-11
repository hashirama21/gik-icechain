// Global MDX component registry (required by @next/mdx in the App Router).
// Maps base HTML elements to the storymap styles + exposes story components so
// MDX authors can write <StoryMap/>, <ScrollyMap/>, <Block/> etc. directly.

import type { MDXComponents } from "mdx/types";
import { Block, Caption, Figure, Prose } from "@/components/story/Blocks";
import StoryMap from "@/components/story/StoryMap";
import ScrollyMap from "@/components/story/ScrollyMap";
import MapWidget from "@/components/map/MapWidget";

export function useMDXComponents(components: MDXComponents): MDXComponents {
  return {
    h1: (props) => <h1 className="story-h1" {...props} />,
    h2: (props) => <h2 {...props} />,
    p: (props) => <p {...props} />,
    Block,
    Prose,
    Figure,
    Caption,
    StoryMap,
    ScrollyMap,
    MapWidget,
    ...components,
  };
}
