"""Code parsing and chunking utilities for semantic code search."""

import ast
import re
from pathlib import Path
from typing import Any

from loguru import logger


class CodeChunk:
    """Represents a chunk of code with metadata."""

    def __init__(
        self,
        code: str,
        file_path: str,
        start_line: int,
        end_line: int,
        language: str,
    ):
        self.code = code
        self.file_path = file_path
        self.start_line = start_line
        self.end_line = end_line
        self.language = language
        self.function_name: str | None = None
        self.class_name: str | None = None
        self.imports: list[str] = []
        self.dependencies: list[str] = []
        self.tags: list[str] = []
        self.complexity_score: float | None = None


class CodeParser:
    """Parser for extracting code structure and creating chunks."""

    # Language detection by file extension
    LANGUAGE_MAP = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "javascript",
        ".tsx": "typescript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "c",
        ".hpp": "cpp",
        ".cs": "csharp",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".kt": "kotlin",
        ".scala": "scala",
        ".r": "r",
        ".m": "objective-c",
        ".sh": "bash",
        ".sql": "sql",
        ".html": "html",
        ".css": "css",
        ".scss": "scss",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
        ".xml": "xml",
        ".md": "markdown",
    }

    def __init__(self, chunk_size: int = 500, overlap_lines: int = 50):
        """Initialize code parser.

        Args:
            chunk_size: Maximum lines per chunk
            overlap_lines: Number of overlapping lines between chunks
        """
        self.chunk_size = chunk_size
        self.overlap_lines = overlap_lines

    def detect_language(self, file_path: str) -> str:
        """Detect programming language from file extension.

        Args:
            file_path: Path to the file

        Returns:
            Language name or 'unknown'
        """
        ext = Path(file_path).suffix.lower()
        return self.LANGUAGE_MAP.get(ext, "unknown")

    def parse_file(self, file_path: str, content: str) -> list[CodeChunk]:
        """Parse a file and create code chunks.

        Args:
            file_path: Path to the file
            content: File content

        Returns:
            List of code chunks
        """
        language = self.detect_language(file_path)

        # Use language-specific parser if available
        if language == "python":
            return self._parse_python(file_path, content)
        else:
            return self._parse_generic(file_path, content, language)

    def _parse_python(self, file_path: str, content: str) -> list[CodeChunk]:
        """Parse Python code with AST analysis.

        Args:
            file_path: Path to the file
            content: File content

        Returns:
            List of code chunks
        """
        chunks: list[CodeChunk] = []
        lines = content.split("\n")

        try:
            tree = ast.parse(content)

            # Extract imports
            imports = self._extract_python_imports(tree)

            # Extract functions and classes
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    chunk = self._create_python_function_chunk(node, lines, file_path, imports)
                    if chunk:
                        chunks.append(chunk)

                elif isinstance(node, ast.ClassDef):
                    chunk = self._create_python_class_chunk(node, lines, file_path, imports)
                    if chunk:
                        chunks.append(chunk)

        except SyntaxError as e:
            logger.warning(f"Syntax error parsing {file_path}: {e}")
            # Fall back to generic parsing
            return self._parse_generic(file_path, content, "python")

        # If no chunks created (e.g., script without functions), create generic chunks
        if not chunks:
            chunks = self._parse_generic(file_path, content, "python")

        return chunks

    def _extract_python_imports(self, tree: ast.AST) -> list[str]:
        """Extract import statements from Python AST.

        Args:
            tree: Python AST

        Returns:
            List of import statements
        """
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        return imports

    def _create_python_function_chunk(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        lines: list[str],
        file_path: str,
        imports: list[str],
    ) -> CodeChunk | None:
        """Create a code chunk for a Python function.

        Args:
            node: Function AST node
            lines: File lines
            file_path: Path to the file
            imports: Import statements

        Returns:
            Code chunk or None
        """
        try:
            start_line = node.lineno
            end_line = node.end_lineno or start_line

            # Get function code
            code_lines = lines[start_line - 1 : end_line]
            code = "\n".join(code_lines)

            chunk = CodeChunk(
                code=code,
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                language="python",
            )

            chunk.function_name = node.name
            chunk.imports = imports

            # Extract decorators as tags
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Name):
                    chunk.tags.append(f"decorator:{decorator.id}")

            # Check if it's async
            if isinstance(node, ast.AsyncFunctionDef):
                chunk.tags.append("async")

            # Calculate simple complexity (number of branches)
            complexity = self._calculate_python_complexity(node)
            chunk.complexity_score = min(complexity / 20.0, 1.0)  # Normalize to 0-1

            return chunk

        except Exception as e:
            logger.warning(f"Error creating function chunk: {e}")
            return None

    def _create_python_class_chunk(
        self,
        node: ast.ClassDef,
        lines: list[str],
        file_path: str,
        imports: list[str],
    ) -> CodeChunk | None:
        """Create a code chunk for a Python class.

        Args:
            node: Class AST node
            lines: File lines
            file_path: Path to the file
            imports: Import statements

        Returns:
            Code chunk or None
        """
        try:
            start_line = node.lineno
            end_line = node.end_lineno or start_line

            # Get class code
            code_lines = lines[start_line - 1 : end_line]
            code = "\n".join(code_lines)

            chunk = CodeChunk(
                code=code,
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                language="python",
            )

            chunk.class_name = node.name
            chunk.imports = imports

            # Extract base classes as tags
            for base in node.bases:
                if isinstance(base, ast.Name):
                    chunk.tags.append(f"extends:{base.id}")

            # Extract decorators
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Name):
                    chunk.tags.append(f"decorator:{decorator.id}")

            # Count methods
            method_count = sum(
                1 for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
            chunk.tags.append(f"methods:{method_count}")

            return chunk

        except Exception as e:
            logger.warning(f"Error creating class chunk: {e}")
            return None

    def _calculate_python_complexity(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
        """Calculate cyclomatic complexity for a Python function.

        Args:
            node: Function AST node

        Returns:
            Complexity score
        """
        complexity = 1  # Base complexity

        for child in ast.walk(node):
            # Count decision points
            if isinstance(
                child,
                (
                    ast.If,
                    ast.While,
                    ast.For,
                    ast.ExceptHandler,
                    ast.With,
                    ast.Assert,
                ),
            ):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1

        return complexity

    def _parse_generic(self, file_path: str, content: str, language: str) -> list[CodeChunk]:
        """Parse code using generic line-based chunking.

        Args:
            file_path: Path to the file
            content: File content
            language: Programming language

        Returns:
            List of code chunks
        """
        chunks: list[CodeChunk] = []
        lines = content.split("\n")
        total_lines = len(lines)

        # Create overlapping chunks
        start = 0
        while start < total_lines:
            end = min(start + self.chunk_size, total_lines)

            # Get chunk code
            chunk_lines = lines[start:end]
            code = "\n".join(chunk_lines)

            # Skip empty chunks
            if code.strip():
                chunk = CodeChunk(
                    code=code,
                    file_path=file_path,
                    start_line=start + 1,
                    end_line=end,
                    language=language,
                )

                # Extract basic metadata
                chunk.imports = self._extract_generic_imports(code, language)
                chunk.tags = self._extract_generic_tags(code, language)

                chunks.append(chunk)

            # Move to next chunk with overlap
            start += self.chunk_size - self.overlap_lines

        return chunks

    def _extract_generic_imports(self, code: str, language: str) -> list[str]:
        """Extract import statements from code (generic).

        Args:
            code: Code content
            language: Programming language

        Returns:
            List of imports
        """
        imports = []

        if language in ("javascript", "typescript"):
            # Match: import ... from '...'
            pattern = r"import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]"
            imports.extend(re.findall(pattern, code))

            # Match: require('...')
            pattern = r"require\(['\"]([^'\"]+)['\"]\)"
            imports.extend(re.findall(pattern, code))

        elif language == "go":
            # Match: import "..."
            pattern = r'import\s+["\']([^"\']+ )["\']'
            imports.extend(re.findall(pattern, code))

        elif language == "rust":
            # Match: use ...;
            pattern = r"use\s+([\w:]+)"
            imports.extend(re.findall(pattern, code))

        elif language == "java":
            # Match: import ...;
            pattern = r"import\s+([\w.]+)"
            imports.extend(re.findall(pattern, code))

        return imports

    def _extract_generic_tags(self, code: str, language: str) -> list[str]:
        """Extract tags from code (generic).

        Args:
            code: Code content
            language: Programming language

        Returns:
            List of tags
        """
        tags = []

        # Check for common patterns
        if "async" in code or "await" in code:
            tags.append("async")

        if "test" in code.lower() or "spec" in code.lower():
            tags.append("test")

        if "class " in code:
            tags.append("class")

        if "function " in code or "def " in code or "fn " in code:
            tags.append("function")

        if "interface " in code:
            tags.append("interface")

        if "type " in code:
            tags.append("type")

        # Language-specific tags
        if language == "python":
            if "@" in code:
                tags.append("decorator")
            if "raise " in code or "except " in code:
                tags.append("error-handling")

        elif language in ("javascript", "typescript"):
            if "export " in code:
                tags.append("export")
            if "Promise" in code:
                tags.append("promise")

        return tags


class CodebaseIndexer:
    """Indexes an entire codebase for semantic search."""

    def __init__(
        self,
        root_path: str,
        file_patterns: list[str],
        exclude_patterns: list[str],
        chunk_size: int = 500,
        overlap_lines: int = 50,
    ):
        """Initialize codebase indexer.

        Args:
            root_path: Root directory to index
            file_patterns: File patterns to include
            exclude_patterns: Patterns to exclude
            chunk_size: Maximum lines per chunk
            overlap_lines: Number of overlapping lines
        """
        self.root_path = Path(root_path)
        self.file_patterns = file_patterns
        self.exclude_patterns = exclude_patterns
        self.parser = CodeParser(chunk_size, overlap_lines)

    def should_exclude(self, path: Path) -> bool:
        """Check if path should be excluded.

        Args:
            path: Path to check

        Returns:
            True if should be excluded
        """
        path_str = str(path)
        return any(pattern in path_str for pattern in self.exclude_patterns)

    def should_include(self, path: Path) -> bool:
        """Check if file should be included.

        Args:
            path: Path to check

        Returns:
            True if should be included
        """
        if not self.file_patterns:
            return True

        return any(path.match(pattern) for pattern in self.file_patterns)

    def index_codebase(self) -> dict[str, Any]:
        """Index the entire codebase.

        Returns:
            Indexing results with chunks and statistics
        """
        all_chunks: list[CodeChunk] = []
        files_indexed = 0
        total_lines = 0
        languages_found: set[str] = set()

        logger.info(f"Starting codebase indexing: {self.root_path}")

        # Walk through directory tree
        for file_path in self.root_path.rglob("*"):
            # Skip directories
            if not file_path.is_file():
                continue

            # Check exclusions
            if self.should_exclude(file_path):
                continue

            # Check inclusions
            if not self.should_include(file_path):
                continue

            try:
                # Read file content
                with file_path.open("r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                # Parse file
                relative_path = str(file_path.relative_to(self.root_path))
                chunks = self.parser.parse_file(relative_path, content)

                if chunks:
                    all_chunks.extend(chunks)
                    files_indexed += 1
                    total_lines += len(content.split("\n"))

                    # Track language
                    language = self.parser.detect_language(relative_path)
                    languages_found.add(language)

                    logger.debug(f"Indexed {relative_path}: {len(chunks)} chunks, {language}")

            except Exception as e:
                logger.warning(f"Error indexing {file_path}: {e}")
                continue

        logger.info(
            f"Indexing complete: {files_indexed} files, {len(all_chunks)} chunks, {total_lines} lines"
        )

        return {
            "chunks": all_chunks,
            "files_indexed": files_indexed,
            "chunks_created": len(all_chunks),
            "total_lines": total_lines,
            "languages_found": sorted(languages_found),
        }
