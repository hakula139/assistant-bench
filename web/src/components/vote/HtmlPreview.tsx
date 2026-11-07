export function HtmlPreview({ url, title }: { url: string; title: string }) {
  return (
    <iframe
      src={url}
      title={title}
      sandbox=""
      referrerPolicy="no-referrer"
      className="h-[min(70dvh,48rem)] min-h-96 w-full border-0 bg-white md:min-h-0"
    />
  );
}
