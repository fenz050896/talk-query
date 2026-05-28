import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { ConnectionProvider } from "@/lib/connection-store";
import { ConversationProvider } from "@/lib/conversation-store";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "TalkQuery — Chat with your Database",
  description: "Ask questions about your data in natural language",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="h-full">
        <ConnectionProvider>
          <ConversationProvider>
            {children}
          </ConversationProvider>
        </ConnectionProvider>
      </body>
    </html>
  );
}
