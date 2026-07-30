import type { ReactNode } from "react";

function inlineContent(text: string, keyPrefix: string): ReactNode[] {
  return text
    .split(/(\*\*[^*]+\*\*|`[^`]+`)/g)
    .filter(Boolean)
    .map((part, index) => {
      const key = `${keyPrefix}-${index}`;
      if (part.startsWith("**") && part.endsWith("**")) {
        return <strong key={key}>{part.slice(2, -2)}</strong>;
      }
      if (part.startsWith("`") && part.endsWith("`")) {
        return <code key={key}>{part.slice(1, -1)}</code>;
      }
      return part;
    });
}

function tableCells(line: string) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function isTableDivider(line: string) {
  const cells = tableCells(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function isTableStart(lines: string[], index: number) {
  return lines[index]?.includes("|") && isTableDivider(lines[index + 1] ?? "");
}

function isListItem(line: string) {
  return /^\s*(?:[-*]|\d+\.)\s+/.test(line);
}

export function ChatAnswer({ children }: { children: string }) {
  const lines = children.trim().split(/\r?\n/);
  const blocks: ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    if (!lines[index].trim()) {
      index += 1;
      continue;
    }

    const heading = lines[index].match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      const Heading = `h${heading[1].length + 2}` as "h3" | "h4" | "h5";
      blocks.push(
        <Heading key={`heading-${index}`}>
          {inlineContent(heading[2], `heading-${index}`)}
        </Heading>,
      );
      index += 1;
      continue;
    }

    if (isTableStart(lines, index)) {
      const start = index;
      const header = tableCells(lines[index]);
      index += 2;
      const rows: string[][] = [];
      while (index < lines.length && lines[index].trim().includes("|")) {
        rows.push(tableCells(lines[index]));
        index += 1;
      }
      blocks.push(
        <div className="chat-table-wrap" key={`table-${start}`}>
          <table className="chat-table">
            <thead>
              <tr>
                {header.map((cell, cellIndex) => (
                  <th key={`head-${cellIndex}`}>
                    {inlineContent(cell, `head-${cellIndex}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={`row-${rowIndex}`}>
                  {header.map((_, cellIndex) => (
                    <td key={`cell-${cellIndex}`}>
                      {inlineContent(row[cellIndex] ?? "", `cell-${rowIndex}-${cellIndex}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    if (isListItem(lines[index])) {
      const start = index;
      const ordered = /^\s*\d+\./.test(lines[index]);
      const items: string[] = [];
      while (index < lines.length && isListItem(lines[index])) {
        items.push(lines[index].replace(/^\s*(?:[-*]|\d+\.)\s+/, ""));
        index += 1;
      }
      const List = ordered ? "ol" : "ul";
      blocks.push(
        <List key={`list-${start}`}>
          {items.map((item, itemIndex) => (
            <li key={`item-${itemIndex}`}>
              {inlineContent(item, `item-${start}-${itemIndex}`)}
            </li>
          ))}
        </List>,
      );
      continue;
    }

    const start = index;
    const paragraph: string[] = [];
    while (
      index < lines.length
      && lines[index].trim()
      && !isTableStart(lines, index)
      && !isListItem(lines[index])
      && !/^(#{1,3})\s+/.test(lines[index])
    ) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(
      <p key={`paragraph-${start}`}>
        {paragraph.map((line, lineIndex) => (
          <span key={`line-${lineIndex}`}>
            {lineIndex > 0 && <br />}
            {inlineContent(line, `line-${start}-${lineIndex}`)}
          </span>
        ))}
      </p>,
    );
  }

  return <div className="chat-answer">{blocks}</div>;
}
