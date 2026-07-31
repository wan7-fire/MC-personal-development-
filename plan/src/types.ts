export type InterfaceIndex = {
  functions: Record<string, FunctionSymbol[]>;
  types: Record<string, TypeSymbol[]>;
};

export type FunctionSymbol = {
  name: string;
  params: ParamSymbol[];
  returnType?: string;
  file: string;
  exported: boolean;
};

export type ParamSymbol = {
  name: string;
  type?: string;
};

export type TypeSymbol = {
  name: string;
  kind: "interface" | "type";
  properties: PropertySymbol[];
  file: string;
  exported: boolean;
};

export type PropertySymbol = {
  name: string;
  type?: string;
  required: boolean;
};

export type CallInfo = {
  callee: string;
  argCount: number;
};

export type ImportInfo = {
  namedImports: string[];
  moduleSpecifier: string;
};

export type SnippetInfo = {
  calls: CallInfo[];
  imports: ImportInfo[];
};

export type AlignmentAction =
  | {
      type: "rewrite-call-to-object";
      callee: string;
      objectType: string;
      fields: string[];
    }
  | {
      type: "add-import";
      symbol: string;
      from: string;
    };

export type AlignmentResult = {
  actions: AlignmentAction[];
  matched: string[];
  warnings: string[];
};
