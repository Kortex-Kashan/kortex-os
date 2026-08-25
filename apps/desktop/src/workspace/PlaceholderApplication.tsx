import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@kortex/design-system";

/**
 * A uniform stub — not real feature UI. Every default application uses
 * this same factory; their actual interfaces ship in later milestones.
 * Split out of defaultApps.ts because it needs JSX, which can't compile
 * in a plain .ts file.
 */
export function createPlaceholderApplication(name: string, description: string) {
  return function PlaceholderApplication() {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{name}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-body text-muted-foreground">
            This application is a placeholder — its real interface ships in a later milestone.
          </p>
        </CardContent>
      </Card>
    );
  };
}
