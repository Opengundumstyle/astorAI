import "./globals.css";

export const metadata = { title: "AstorScientific", description: "AI-native procurement" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
