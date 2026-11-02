import { formatPrice } from "../lib/format";

const LINKS = [
  { href: "/shop", label: "Shop" },
  { href: "/services", label: "Services" },
  { href: "/blog", label: "Blog" },
  { href: "/contact", label: "Contact" },
];

export function Footer() {
  return (
    <footer className="border-t bg-white py-8 text-slate-600">
      <div className="mx-auto max-w-6xl px-4">
        <p className="font-semibold">Kenya's borehole drilling partner</p>
        <p>Site surveys from {formatPrice(15000)}</p>
        <nav className="mt-4 flex gap-4">
          {LINKS.map((link) => (
            <a key={link.href} href={link.href}>
              {link.label}
            </a>
          ))}
        </nav>
        <p className="mt-6 text-sm">© 2026 Example Drilling Ltd. All rights reserved.</p>
      </div>
    </footer>
  );
}
