/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useState, type ReactNode } from "react";
import { Accordion, AccordionPanel, AccordionTrigger } from "@backstage/ui";

/** Expensive diff/log/tree content mounts only when the user opens it. */
export function DisclosureSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <Accordion isExpanded={expanded} onExpandedChange={setExpanded}>
      <AccordionTrigger title={title} />
      <AccordionPanel>{expanded ? children : null}</AccordionPanel>
    </Accordion>
  );
}
