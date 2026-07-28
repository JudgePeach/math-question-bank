%
% Copyright (c) 2024 Xia Kangwei and Zeping Lee
% Released under the LaTeX Project Public License v1.3c License.
% Repository: https://github.com/xkwxdyy/exam-zh
%            https://gitee.com/xkwxdyy/exam-zh (mirror)
%

\NeedsTeXFormat{LaTeX2e}[2017/04/15]
\RequirePackage{expl3}
\ProvidesExplClass {exam-zh} {2026-07-12} {v0.3.0} {LaTeX template for Chinese exam}

% 检查 LaTeX2e kernel 版本
\msg_new:nnn { exam-zh } { latex-too-old }
  { TeX~ Live~ 2020~ or~ later~ version~ is~ required~ to~ compile~ this~ document. }
\@ifl@t@r \fmtversion { 2020/02/02 }
  { }
  { \msg_fatal:nn { exam-zh } { latex-too-old } }

% 检查编译引擎，要求使用 XeLaTeX。
\msg_new:nnn { exam-zh } { incompatible-engine }
  { XeLaTeX~ is~ required~ to~ compile~ this~ document. }

\sys_if_engine_xetex:F
  { \msg_fatal:nn { exam-zh } { incompatible-engine } }


% 使用 l3keys 定义 \examsetup 配置命令
\NewDocumentCommand \examsetup { m }
  { \keys_set:nn { exam-zh } {#1} }



% 加载文档类和宏包

% 处理文档类选项
\PassOptionsToClass { UTF8 , scheme = chinese, openany } { ctexbook }
\DeclareOption* { \PassOptionsToClass { \CurrentOption } { ctexbook } }
\ProcessOptions*

\RequirePackage { filehook }
\AtEndOfPackageFile* { fontspec }
  { \msg_redirect_name:nnn { fontspec } { no-script } { none } }
\AtEndOfPackageFile* { xeCJK }
  {
    \msg_redirect_name:nnn { xeCJK } { CJKfamily-redef } { none }
    \defaultCJKfontfeatures
      {
        Script  = CJK,
      }
  }

% 载入 \cls{ctexbook} 文档类。
\LoadClass { ctexbook }

% 要求 ctex v2.4.9 2017-04-01 或更高的版本。
\msg_new:nnn { exam-zh } { require-package-version }
  { The~ package~ "#1"~ is~ required. }

\@ifclasslater { ctexbook } { 2017/04/01 }
  { }
  {
    \msg_fatal:nnn { exam-zh } { require-package-version }
      { ctex~ v2.4.9~ 2017-04-01 }
  }

% 建议在模板开始处载入全部宏包，不要轻易改变加载顺序。
\RequirePackage { etoolbox }
\RequirePackage { geometry }
\RequirePackage { fontspec }
\RequirePackage { xeCJK }
\RequirePackage { xeCJKfntef }
\RequirePackage { fancyhdr }
\RequirePackage { lastpage }
\RequirePackage { amsmath }
\RequirePackage { enumitem }
\RequirePackage { varwidth }

\ExplSyntaxOff
\RequirePackage { tikzpagenodes }
\usetikzlibrary { decorations.markings }
\usetikzlibrary { decorations.text }
\ExplSyntaxOn

\RequirePackage { exam-zh-question }
\RequirePackage { exam-zh-font }
\RequirePackage { exam-zh-choices }
\RequirePackage { exam-zh-symbols }
\RequirePackage { exam-zh-chinese-english }
\RequirePackage { exam-zh-textfigure }
\RequirePackage { exam-zh-math }

\AtEndPreamble
  {
    \RequirePackage { hyperref }
    \hypersetup
      {
        bookmarksnumbered = true,
        psdextra          = true,
        unicode           = true,
        hidelinks
      }
  }


% 对冲突的宏包报错。
\msg_new:nnn { exam-zh } { package-conflict }
  { The~ "#2"~ package~ is~ incompatible~ with~ "#1". }

\cs_new:Npn \examzh_package_conflict:nn #1#2
  {
    \AtEndOfPackageFile* {#1}
      {
        \AtBeginOfPackageFile* {#2}
          { \msg_error:nnnn { exam-zh } { package-conflict } {#1} {#2} }
      }
  }

\examzh_package_conflict:nn { unicode-math } { amscd }
\examzh_package_conflict:nn { unicode-math } { amsfonts }
\examzh_package_conflict:nn { unicode-math } { amssymb }
\examzh_package_conflict:nn { unicode-math } { bbm }
\examzh_package_conflict:nn { unicode-math } { bm }
\examzh_package_conflict:nn { unicode-math } { eucal }
\examzh_package_conflict:nn { unicode-math } { eufrak }
\examzh_package_conflict:nn { unicode-math } { mathrsfs }
\examzh_package_conflict:nn { unicode-math } { newtxmath }
\examzh_package_conflict:nn { unicode-math } { upgreek }

\examzh_package_conflict:nn { enumitem } { paralist }


% 生成函数变体
\cs_generate_variant:Nn \tl_map_inline:nn { xn }

% 标点处理
\tl_const:Nn \c__examzh_fwid_full_stop_tl { ^^^^ff0e }

\keys_define:nn { exam-zh }
  { style .meta:nn = { exam-zh / style } {#1} }
\keys_define:nn { exam-zh / style }
  {
    fullwidth-stop .choice:,
    fullwidth-stop .value_required:n = true,
    fullwidth-stop / catcode .code:n =
      { 
        \__examzh_set_fullwidth_stop_catcode:
      },
    fullwidth-stop / false .code:n = { }
  }
\cs_new:Npn \__examzh_set_fullwidth_stop_catcode:
  {
    \char_set_active_eq:NN ^^^^3002 \c__examzh_fwid_full_stop_tl
    \char_set_catcode_active:N ^^^^3002
  }

\keys_set:nn { exam-zh / style }
  {
    fullwidth-stop = false
  }

\keys_set:nn { ctex }
  {
    % 修改目录层级
    tocdepth = 0,
    chapter = 
      {
        numbering   = false,
        beforeskip  = -4ex,
        afterskip   = 4ex minus 1ex
      }
  }

% 师生两版

% 用户自定义统一控制的预设 (将所需要控制的键值进行预设置)

\NewDocumentCommand { \ExamPrintAnswerSet } { O{} m }
  % #1: 命令
  % #2: 键值  foo/bar
  {
    \clist_const:Nn \g__examzh_print_answer_cmd_set_clist {#1}
    \clist_const:Nn \g__examzh_print_answer_keys_set_clist {#2}
  }

\NewDocumentCommand { \ExamPrintAnswer } { }
  {
    \clist_if_exist:NT \g__examzh_print_answer_cmd_set_clist
      { \clist_use:Nn \g__examzh_print_answer_cmd_set_clist {} }
    \clist_if_exist:NT \g__examzh_print_answer_keys_set_clist
      {
        \keys_set:nx { exam-zh }
          { \g__examzh_print_answer_keys_set_clist }
      }
  }



\str_new:N \l__examzh_latexmk_engine_str
\str_new:N \g__examzh_student_version_jobname_str
\str_new:N \l__examzh_student_version_suffix_str
\str_const:Nn \l__examzh_latexmk_str { latexmk }

\keys_define:nn { exam-zh / style }
  {
    student-version-suffix .code:n = 
      { \str_set:Nn \l__examzh_student_version_suffix_str {#1} },
    student-version-cleanaux .bool_set:N = \l__examzh_student_version_clean_aux_bool
  }
\keys_set:nn { exam-zh / style }
  {
    student-version-suffix   = { _student_version },
    student-version-cleanaux = true
  }

\cs_new:Nn \__examzh_build_student_version_jobname:
  {
    \str_gset:Nx \g__examzh_student_version_jobname_str { \c_sys_jobname_str }
    \str_gremove_all:Nn \g__examzh_student_version_jobname_str { " }
    \str_gput_left:Nn \g__examzh_student_version_jobname_str { " }
    \str_gput_right:Nx \g__examzh_student_version_jobname_str { \l__examzh_student_version_suffix_str }
    \str_gput_right:Nn \g__examzh_student_version_jobname_str { " }
  }

\AtEndPreamble
  {
    \sys_if_shell_unrestricted:T
      {
        \str_set:Nn \l__examzh_latexmk_engine_str { -xelatex }
        \__examzh_build_student_version_jobname:
        \sys_shell_now:x
          {
            \l__examzh_latexmk_str \c_space_tl
            \l__examzh_latexmk_engine_str \c_space_tl
            -pretex='
              % \string\RequirePackage{etoolbox}
              % \string\BeforeBeginEnvironment{document}{\string\ExamPrintAnswer}
              \string\AddToHook{env/document/before}{\string\ExamPrintAnswer}
              % \string\AtBeginDocument{\string\ExamPrintAnswer}
            ' \c_space_tl
            -usepretex \c_space_tl
            -jobname=\g__examzh_student_version_jobname_str \c_space_tl
            \c_sys_jobname_str
            \bool_if:NT \l__examzh_student_version_clean_aux_bool
              {
                &&
                \l__examzh_latexmk_str \c_space_tl
                \l__examzh_latexmk_engine_str \c_space_tl
                -c \c_space_tl
                -jobname=\g__examzh_student_version_jobname_str \c_space_tl
                \c_sys_jobname_str
              }
          }
        \sys_shell_now:x
          {
            \l__examzh_latexmk_str \c_space_tl
            \l__examzh_latexmk_engine_str \c_space_tl
            \c_sys_jobname_str
            \bool_if:NT \l__examzh_student_version_clean_aux_bool
              {
                &&
                \l__examzh_latexmk_str \c_space_tl
                \l__examzh_latexmk_engine_str \c_space_tl
                -c \c_space_tl
                \c_sys_jobname_str
              }
          }
        \stop
      }
  }


% 处理目录
\patchcmd { \tableofcontents }
  { \@starttoc{toc} }
  {
    \thispagestyle { empty } 
    \pagestyle { empty }
    \@starttoc{toc} 
  }
  {}{\fail}
\cs_set_eq:NN \t@bleofcontents \tableofcontents
\RenewDocumentCommand { \tableofcontents } { }
  {
    \newpage
    \int_set:Nn \c@page { 0 }
    \group_begin:
      % \str_case:VnT \g__examzh_sealline_scope_str
      %   {
      %     { everypage } {}
      %     { oddpage } {}
      %   }
      %   {
      %     \RemoveFromHook { shipout / background }
      \cs_set_eq:NN \onecolumn \twocolumn
      \bool_set_true:N \g__examzh_page_show_chapter_bool
      \keys_set:nn { ctex }
        {
          chapter = 
            {
              beforeskip  = 1pt,
              afterskip   = 2ex minus 1ex
            }
        }
      \t@bleofcontents
    \group_end:
    \newpage
    % 将页码计数器重设置为 1
    \int_set:Nn \c@page { 1 }
  }

\AtEndPreamble
  {
    % A3 情况 separate 的目录页码需要改为 2* -1
    \bool_lazy_and:nnT
      {
        ! \bool_if_p:c { g__examzh_page_size_a4paper_bool }
      }
      {
        ! \bool_if_p:c { g__examzh_page_a3paper_foot_common_bool }
      }
      {
        \cs_set_eq:Nc \addcontentsline { __examzh_addcontentsline_a3paper_separate:nnn }
      }
  }
% https://tex.stackexchange.com/questions/650823/why-my-patch-to-addcontentsline-is-broken-in-atendpreamble-and-bool-ifnt
% 改自 hyperref.sty
\cs_set:cpn { __examzh_addcontentsline_a3paper_separate:nnn } #1#2#3
  {
    \begingroup
      \let\label\@gobble
      \ifx\@currentHref\@empty
        \Hy@Warning{
          No destination for bookmark of \string\addcontentsline,%
          \MessageBreak destination is added%
        }
        \phantomsection
      \fi
      \expandafter\ifx\csname toclevel@#2\endcsname\relax
        \begingroup
          \def\Hy@tempa{#1}
          \ifx\Hy@tempa\Hy@bookmarkstype
            \Hy@WarningNoLine{
              bookmark level for unknown #2 defaults to 0%
            }
          \else
            \Hy@Info{bookmark level for unknown #2 defaults to 0}%
          \fi
        \endgroup
        \expandafter\gdef\csname toclevel@#2\endcsname{0}%
      \fi
      \edef\Hy@toclevel{\csname toclevel@#2\endcsname}%
      \Hy@writebookmark{\csname the#2\endcsname}%
        {#3}
        {\@currentHref}
        {\Hy@toclevel}
        {#1}
      \ifHy@verbose
        \begingroup
          \def\Hy@tempa{#3}
          \@onelevel@sanitize\Hy@tempa
          \let\temp@online\on@line
          \let\on@line\@empty
          \Hy@Info{
            bookmark\temp@online:\MessageBreak
            thecounter {\csname the#2\endcsname}\MessageBreak
            text {\Hy@tempa}\MessageBreak
            reference {\@currentHref}\MessageBreak
            toclevel {\Hy@toclevel}\MessageBreak
            type {#1}
          }
        \endgroup
      \fi
      \addtocontents{#1}{
        \protect\contentsline{#2}{#3}
          % {\thepage}
          { \int_eval:n { 2 * \c@page - 1 } }
          {\@currentHref}\protected@file@percent
      }
    \endgroup
  }
\keys_define:nn { exam-zh / page }
  {
    show-chapter .bool_gset:N = \g__examzh_page_show_chapter_bool
  }
\keys_set:nn { exam-zh / page }
  {
    show-chapter = true
  }

\cs_set_eq:NN \__examzh_chapter:nn \chapter
\cs_new:Npn \__examzh_chapter_star:n #1
  {
    \__examzh_chapter:nn * {#1}
  }
\RenewDocumentCommand{ \chapter }{ s o m }
  {
    \keys_set:nn { exam-zh / question }
      { index = 1 }
    \int_set:Nn \c@section { 0 }
    \bool_if:NTF \g__examzh_page_show_chapter_bool
      {
        \IfBooleanTF {#1}
          { \__examzh_chapter_star:n {#3} }
          {
            \IfNoValueTF {#2}
              { \__examzh_chapter:nn {#3} }
              { \__examzh_chapter:nn [#2] {#3} }
          }
        \pagestyle { plain }
      }
      {
        \IfBooleanF {#1}
          {
            \newpage
            % 只录入目录
            \phantomsection
            \addcontentsline { toc } { chapter } {#3}
            \pagestyle { plain }
          }
        \clearpage
      }
    % 修复 section 的超链接跳转问题
    \int_gincr:N \c@chapter
  }

% 纸张和页面布局

\msg_new:nnn { exam-zh } { custom-paper-width-required }
  { page/paper-width~must~be~positive~when~page/size=custom. }
\msg_new:nnn { exam-zh } { custom-paper-height-required }
  { page/paper-height~must~be~positive~when~page/size=custom. }

% 控制 a3paper 和常规单页纸张（a4paper 或 custom）
\bool_new:c { g__examzh_page_size_a4paper_bool }
% custom 纸张使用常规单页的页脚模型，而不是 a3paper 的双页拼版模型
\bool_new:N \g__examzh_page_size_custom_bool
\dim_new:N \g__examzh_page_paper_width_dim
\dim_new:N \g__examzh_page_paper_height_dim
\dim_new:N \g__examzh_page_margin_dim
\dim_new:N \g__examzh_page_headheight_dim
% a3paper 的页脚：两页共用一个或者仍然一页一个
\bool_new:c { g__examzh_page_a3paper_foot_common_bool }

\keys_define:nn { exam-zh / page }
  {
    % 页面大小
    size .choice:,
    size / a3paper .code:n =
      {
        \bool_gset_false:c { g__examzh_page_size_a4paper_bool }
        \bool_gset_false:N \g__examzh_page_size_custom_bool
      },
    size / a4paper .code:n =
      {
        \bool_gset_true:c { g__examzh_page_size_a4paper_bool }
        \bool_gset_false:N \g__examzh_page_size_custom_bool
      },
    size / custom .code:n =
      {
        \bool_gset_true:c { g__examzh_page_size_a4paper_bool }
        \bool_gset_true:N \g__examzh_page_size_custom_bool
      },
    size .value_required:n = true,
    paper-width .dim_gset:N = \g__examzh_page_paper_width_dim,
    paper-height .dim_gset:N = \g__examzh_page_paper_height_dim,
    margin .dim_gset:N = \g__examzh_page_margin_dim,
    headheight .dim_gset:N = \g__examzh_page_headheight_dim,
    % 页脚的类型
    foot-type .choice:,
    foot-type / common .code:n =
      {
        \bool_gset_true:c 
          { g__examzh_page_a3paper_foot_common_bool }
      },
    foot-type / separate .code:n =
      {
        \bool_gset_false:c 
          { g__examzh_page_a3paper_foot_common_bool }
      }
  }
\keys_set:nn { exam-zh / page }
  {
    size = a4paper,
    paper-width = 0pt,
    paper-height = 0pt,
    margin = 1in,
    headheight = 0.7in,
    foot-type = separate,
  }

\keys_define:nn { exam-zh }
  {
    page .meta:nn = { exam-zh / page } {#1} 
  }


% 字体

% 中文字体

% 在 ctex 的字体配置的基础上进行一些修改
% 将苹方和微软雅黑分别替换为华文黑体和中易黑体

\str_if_eq:onTF { \g__ctex_fontset_tl } { mac }
  {
    % \setCJKmainfont{Source~Han~Serif~SC}
    %   [
    %     ItalicFont     = FZKai-Z03,
    %   ]
    \setCJKsansfont { Heiti~ SC~ Light } [ BoldFont = Heiti~ SC~ Medium ]
  }
  {
    \str_if_eq:onT { \g__ctex_fontset_tl } { windows }
      { 
        % \setCJKmainfont{Source~Han~Serif~SC}
        %   [
        %     ItalicFont     = FZKai-Z03,
        %   ]
        \setCJKsansfont { SimHei } 
      }
  }

% 罗马数字使用中文字体
\xeCJKDeclareCharClass { CJK } { "2160 -> "217F }
% 带圈数字
\xeCJKDeclareCharClass { CJK } { "2460 -> "2473 }


% 如果有内容较高（如分式）使得行间距小于 0.5em，则将其增加至 0.5em。
\dim_set:Nn \lineskiplimit { .5em }
\skip_set:Nn \lineskip { .5em }

% 增加对 minipage 的处理
% https://tex.stackexchange.com/a/358080/246645
\dim_set:Nn \normallineskiplimit { .5em }
\skip_set:Nn \normallineskip { .5em }



% 设置 enumitem 列表格式
\keys_define:nn { exam-zh / list }
  {
    step-name .tl_set:N = \l__examzh_list_step_name_tl,
    method-name .tl_set:N = \l__examzh_list_method_name_tl,
    case-name .tl_set:N = \l__examzh_list_case_name_tl,
    step-punct .tl_set:N = \l__examzh_list_step_punct_tl,
    method-punct .tl_set:N = \l__examzh_list_method_punct_tl,
    case-punct .tl_set:N = \l__examzh_list_case_punct_tl,
  }
\keys_set:nn { exam-zh / list }
  {
    step-name     = 步骤,
    method-name   = 方法,
    case-name     = 情形,
    step-punct    = .,
    method-punct  = ,
    case-punct    = .,
  }
\keys_define:nn { exam-zh }
  { list .meta:nn = { exam-zh / list } {#1} }

\setlist{nosep}
\setlist
  {
    labelsep    = 2pt,
  }

\setlist[enumerate, 1]
  {
    labelindent = \parindent,
    labelsep    = 4pt,
    leftmargin  = *,
    % label       = { \arabic * .}
    label       = {（ \arabic * ）},
  }

\setlist[enumerate, 2]
  {
    % labelindent = *,
    leftmargin  = 2em, 
    widest      = 0,
    itemindent  = 0em,
    labelsep    = 0pt,
    % labelwidth  = 2em,
    listparindent = \parindent,
    label       = {（ \alph * ）},
  }
\setlist[enumerate, 3]
  {
    % labelindent = *,
    leftmargin  = 2em, 
    widest      = 0,
    itemindent  = 0em,
    labelsep    = 0pt,
    % labelwidth  = 2em,
    listparindent = \parindent,
    label       = {（ \roman * ）},
  }
% 多种方法: method环境
\newlist{method}{enumerate}{1}
\setlist[method, 1]
  {
    label = {\bfseries \l__examzh_list_method_name_tl \zhnum*\l__examzh_list_method_punct_tl},
    labelindent = !,
    labelwidth  = 1.3cm,
    labelsep*   = 0.5em,
    leftmargin  = 1.4cm
  }
% 分类讨论: case环境
\newlist{case}{enumerate}{2}
\setlist[case, 1]
  {
    label = {\bfseries \l__examzh_list_case_name_tl \arabic*\l__examzh_list_case_punct_tl},
    % labelindent=-3em ,labelwidth=1.3cm, labelsep*=1em, leftmargin=20pt
    labelindent = !,
    labelwidth  = 1.3cm,
    labelsep*   = 0.5em,
    leftmargin  = 1.7cm
    % labelindent=\parindent, leftmargin=0pt, widest=0, itemindent=*
  }
\setlist[case, 2]
  {
    label = {\bfseries \l__examzh_list_case_name_tl \arabic{casei}.\arabic*\l__examzh_list_case_punct_tl},
    % labelindent=-1em ,labelwidth=1.3cm, labelsep*=1em, leftmargin =20pt
    labelindent = -0.5em,
    labelwidth  = 1.3cm,
    labelsep*   = 0.5em,
    leftmargin  = 0cm
  }
% 分步: step环境
\newlist{step}{enumerate}{2}
\setlist[step, 1]
  {
    label = {\bfseries \l__examzh_list_step_name_tl \arabic*\l__examzh_list_step_punct_tl},
    labelindent = !,
    labelwidth  = 1.3cm,
    labelsep*   = 0.5em,
    leftmargin  = 1.7cm
    % labelindent=\parindent, leftmargin = 0pt, widest = 0, itemindent = *
  }
\setlist[step, 2]
  {
    label       =  {\bfseries \l__examzh_list_step_name_tl \arabic{stepi}.\arabic*\l__examzh_list_step_punct_tl},
    labelindent = -0.5em,
    labelwidth  = 1.3cm,
    labelsep*   = 0.5em, 
    leftmargin  = 0cm
  }

% 横向的个人信息

\clist_new:N \l__examzh_horizontal_information_clist

\NewDocumentCommand \information { O{\quad} m }
% #1 分隔符
% #2 信息内容
  {
    \clist_set:Nn \l__examzh_horizontal_information_clist {#2}
    \__examzh_print_horizontal_information:n {#1}
  }
\cs_new:Npn \__examzh_print_horizontal_information:n #1
  {
    \par \null \hfill
    \clist_use:Nn \l__examzh_horizontal_information_clist {#1}
    \hfill \null \par
  }

% 警告，如“在此卷上答题无效”
\NewDocumentCommand \warning { O{\large \sffamily \bfseries} m }
  {
    \group_begin:
      #1
      \hfill #2 \hfill \null
    \group_end:
    \par
  }


% 标题处理
\keys_define:nn { exam-zh / title }
  {
    title-format .tl_set:N = \l__examzh_title_format_tl,
    subject-format .tl_set:N = \l__examzh_subject_format_tl,
    top-sep .skip_set:N = \l__examzh_title_top_sep_skip,
    bottom-sep .skip_set:N = \l__examzh_title_bottom_sep_skip,
  }
\keys_set:nn { exam-zh / title }
  {
    title-format   = \Large,
    subject-format = \sffamily \bfseries \huge,
    top-sep        = -.5em plus 0.3em minus 0.2em,
    bottom-sep     = 0em plus 0.3em minus 0.2em,
  }
\keys_define:nn { exam-zh }
  { title .meta:nn = { exam-zh / title } {#1} }

\cs_new_protected:Npn \__examzh_spread_box:nn #1#2
  {
    \mode_leave_vertical:
    \hbox_to_wd:nn {#1}
      { \tl_map_inline:xn {#2} { ##1 \hfil } \unskip }
  }

% 科目
\tl_new:N \l__examzh_subject_tl
\NewDocumentCommand \subject { o m }
  {
    \IfNoValueTF {#1}
      {
        % 默认使用自然宽度
        \hbox_set:Nn \l_tmpa_box {#2}
        \dim_set:Nn \l_tmpa_dim { \box_wd:N \l_tmpa_box * 2 }
        \tl_set:Nn \l__examzh_subject_tl
          {
            \__examzh_spread_box:nn { \l_tmpa_dim } {#2}
          }
      }
      {
        % 手动调整宽度
        \tl_set:Nn \l__examzh_subject_tl
          {
            \__examzh_spread_box:nn {#1} {#2}
          }
      }
  }

% 输出标题
\RenewDocumentCommand \maketitle { }
  {
    \par
    \vspace { \l__examzh_title_top_sep_skip }
    \begin { center }
      \let \footnote \thanks
      { \l__examzh_title_format_tl \@title \par }
      \tl_if_blank:VF \l__examzh_subject_tl
        {
          \addvspace { 1em }
          { \l__examzh_subject_format_tl \l__examzh_subject_tl }
        }
    \end { center }
    \par
    \vspace { \l__examzh_title_bottom_sep_skip }
  }



\prg_new_conditional:Npnn \examzh_if_defined:N #1 { T , F , TF }
  {
    \if_meaning:w #1 \@undefined
      \prg_return_false:
    \else:
      \prg_return_true:
    \fi:
  }

% 绝密 启用前
\NewDocumentCommand \secret { O{\bfseries} }
  {
    \par \noindent
    \group_begin:
      #1
      绝密 $\bigstar$ 启用前
    \group_end:
    \par
  }


% 注意事项环境 notice
\keys_define:nn { exam-zh / notice }
  {
    label .tl_set:N = \l__examzh_notice_label_tl,
    label-format .tl_set:N = \l__examzh_notice_label_format_tl,
    top-sep    .skip_set:N = \l__examzh_notice_top_sep_skip,
    bottom-sep .skip_set:N = \l__examzh_notice_bottom_sep_skip,
    
  }
\keys_set:nn { exam-zh / notice }
  {
    label           = 注意事项：,
    label-format    = \sffamily \bfseries,
    top-sep         = .25em plus .25em minus .1em,
    bottom-sep      = .25em plus .25em minus .1em,
  }
\NewDocumentEnvironment { notice } { O { } O { } }
  {
    \keys_set:nn { exam-zh / notice } {#1}
    \par
    \vspace { \l__examzh_notice_top_sep_skip }
    \noindent
    \group_begin:
      \l__examzh_notice_label_format_tl
      \l__examzh_notice_label_tl
    \group_end:
    \begin { enumerate }
      [
        leftmargin = 0pt ,
        itemindent = 3.5em ,
        labelsep   = 0.5em ,
        labelwidth = 1.5em ,
        align      = right ,
        label      = { \arabic * . } ,
        #2
      ]
  }
  {
    \end { enumerate }
    \vspace { \l__examzh_notice_bottom_sep_skip }
  }


% 大题的标题使用 \ctexset 设置 \section 的格式

\ctexset
  {
    section =
      {
        format    = \heiti \bfseries ,
        number    = \chinese { section } ,
        aftername = { 、 } ,
        beforeskip = 2ex plus 1ex minus .5ex,
        afterskip = 1ex plus .2ex minus 1ex
      }
  }



% 兼容 siunitx v2.x 的一些命令
\AtEndOfPackageFile* { siunitx }
  {
    \ProvideDocumentCommand \unit       { } { \si }
    \ProvideDocumentCommand \qty        { } { \SI }
    \ProvideDocumentCommand \qtyproduct { } { \SI }
  }



% 密封线
\str_new:N \g__examzh_sealline_odd_type_str
\str_new:N \g__examzh_sealline_even_type_str

\keys_define:nn { exam-zh / sealline }
  {
    % 是否显示密封线
    show .bool_gset:N = \g__examzh_sealline_show_bool,
    % 密封线类型（也理解为出现的类型，比如只在第一页，只在奇数页）
    scope .choices:nn =
      { firstpage, oddpage, everypage, first-and-last, mod-2, mod-3, mod-4, mod-6 }
      { \str_gset:Nx \g__examzh_sealline_scope_str { \l_keys_choice_tl } },
    type .choices:nn =
      { firstpage, oddpage, everypage, first-and-last, mod-2, mod-3, mod-4, mod-6 }
      { \str_gset:Nx \g__examzh_sealline_scope_str { \l_keys_choice_tl } },
    % 线的厚度
    odd-line-thickness .dim_set:N = \g__examzh_sealline_odd_line_thickness_dim,
    even-line-thickness .dim_set:N = \g__examzh_sealline_even_line_thickness_dim,
    line-thickness .code:n =
      {
        \dim_set:Nn \g__examzh_sealline_odd_line_thickness_dim {#1}
        \dim_set:Nn \g__examzh_sealline_even_line_thickness_dim {#1}
      },
    % 线的偏移
    odd-line-xshift .dim_set:N = \g__examzh_sealline_odd_line_xshift_dim,
    odd-line-yshift .dim_set:N = \g__examzh_sealline_odd_line_yshift_dim,
    even-line-xshift .dim_set:N = \g__examzh_sealline_even_line_xshift_dim,
    even-line-yshift .dim_set:N = \g__examzh_sealline_even_line_yshift_dim,
    line-xshift .code:n =
      {
        \dim_gset:Nn \g__examzh_sealline_odd_line_xshift_dim {#1}
        \dim_gset:Nn \g__examzh_sealline_even_line_xshift_dim {#1}
      },
    line-yshift .code:n =
      {
        \dim_set:Nn \g__examzh_sealline_odd_line_yshift_dim {#1}
        \dim_set:Nn \g__examzh_sealline_even_line_yshift_dim {#1}
      },
    % 密封线的线类型
    % odd-line-type .str_set:N = \g__examzh_sealline_odd_type_str,
    % even-line-type .str_set:N = \g__examzh_sealline_even_type_str,
    odd-line-type .code:n =
      {
        \str_set:Nn \g__examzh_sealline_odd_type_str {#1}
      },
    even-line-type .code:n =
      {
        \str_set:Nn \g__examzh_sealline_even_type_str {#1}
      },
    line-type .code:n = 
      {
        \str_gset:Nn \g__examzh_sealline_odd_type_str {#1}
        \str_gset:Nn \g__examzh_sealline_even_type_str {#1}
      },
    % 密封线路径的文字内容
    odd-text .tl_set:N = \g__examzh_sealline_odd_text_tl,
    even-text .tl_set:N = \g__examzh_sealline_even_text_tl,
    text .code:n = 
      {
        \tl_gset:Nn \g__examzh_sealline_odd_text_tl {#1}
        \tl_gset:Nn \g__examzh_sealline_even_text_tl {#1}
      },
    % 密封线路径的文字内容偏移
    odd-text-xshift .dim_set:N = \g__examzh_sealline_odd_text_xshift_dim,
    even-text-xshift .dim_set:N = \g__examzh_sealline_even_text_xshift_dim,
    odd-text-yshift .dim_set:N = \g__examzh_sealline_odd_text_yshift_dim,
    even-text-yshift .dim_set:N = \g__examzh_sealline_even_text_yshift_dim,
    text-xshift .code:n = 
      {
        \dim_gset:Nn \g__examzh_sealline_odd_text_xshift_dim {#1}
        \dim_gset:Nn \g__examzh_sealline_even_text_xshift_dim {#1}
      },
    text-yshift .code:n = 
      {
        \dim_gset:Nn \g__examzh_sealline_odd_text_yshift_dim {#1}
        \dim_gset:Nn \g__examzh_sealline_even_text_yshift_dim {#1}
      },
    % 密封线路径文字的宽度
    odd-text-width .dim_set:N = \g__examzh_sealline_odd_text_width_dim,
    even-text-width .dim_set:N = \g__examzh_sealline_even_text_width_dim,
    text-width .code:n = 
      {
        \dim_gset:Nn \g__examzh_sealline_odd_text_width_dim {#1}
        \dim_gset:Nn \g__examzh_sealline_even_text_width_dim {#1}
      },
    % 密封线路径文字的字体
    odd-text-format .tl_set:N = \g__examzh_sealline_odd_text_format_tl,
    even-text-format .tl_set:N = \g__examzh_sealline_even_text_format_tl,
    text-format .code:n = 
      {
        \tl_gset:Nn \g__examzh_sealline_odd_text_format_tl {#1}
        \tl_gset:Nn \g__examzh_sealline_even_text_format_tl {#1}
      },
    % 密封线路径文字的方向
    odd-text-direction-vertical .bool_gset:N = \g__examzh_sealline_odd_text_direction_vertical_bool,
    even-text-direction-vertical .bool_gset:N = \g__examzh_sealline_even_text_direction_vertical_bool,
    text-direction-vertical .choice:, 
    text-direction-vertical / true .code:n = 
      {
        \bool_gset_true:N \g__examzh_sealline_odd_text_direction_vertical_bool
        \bool_gset_true:N \g__examzh_sealline_even_text_direction_vertical_bool
      },
    text-direction-vertical / false .code:n =
      {
        \bool_gset_false:N \g__examzh_sealline_odd_text_direction_vertical_bool
        \bool_gset_false:N \g__examzh_sealline_even_text_direction_vertical_bool
      },
    % 密封线路径文字的防缩比例
    odd-text-xscale .fp_set:N = \g__examzh_sealline_odd_text_xscale_fp,
    odd-text-yscale .fp_set:N = \g__examzh_sealline_odd_text_yscale_fp,
    even-text-xscale .fp_set:N = \g__examzh_sealline_even_text_xscale_fp,
    even-text-yscale .fp_set:N = \g__examzh_sealline_even_text_yscale_fp,
    text-xscale .code:n = 
      {
        \fp_gset:Nn \g__examzh_sealline_odd_text_xscale_fp {#1}
        \fp_gset:Nn \g__examzh_sealline_even_text_xscale_fp {#1}
      },
    text-yscale .code:n = 
      {
        \fp_gset:Nn \g__examzh_sealline_odd_text_yscale_fp {#1}
        \fp_gset:Nn \g__examzh_sealline_even_text_yscale_fp {#1}
      },
    % 密封线上圆圈的参数
    % --控制显示--
    odd-circle-show .bool_set:N = \g__examzh_sealline_odd_circle_show_bool,
    even-circle-show .bool_set:N = \g__examzh_sealline_even_circle_show_bool,
    circle-show .choice:,
    circle-show / true .code:n =
      {
        \bool_gset_true:N \g__examzh_sealline_odd_circle_show_bool
        \bool_gset_true:N \g__examzh_sealline_even_circle_show_bool
      },
    circle-show / false .code:n =
      {
        \bool_gset_false:N \g__examzh_sealline_odd_circle_show_bool
        \bool_gset_false:N \g__examzh_sealline_even_circle_show_bool
      },
    % --开始--
    odd-circle-start .fp_set:N = \g__examzh_sealline_odd_circle_start_fp,
    even-circle-start .fp_set:N = \g__examzh_sealline_even_circle_start_fp,
    circle-start .code:n = 
      {
        \fp_gset:Nn \g__examzh_sealline_odd_circle_start_fp {#1}
        \fp_gset:Nn \g__examzh_sealline_even_circle_start_fp {#1}
      },
    % --结束--
    odd-circle-end .fp_set:N = \g__examzh_sealline_odd_circle_end_fp,
    even-circle-end .fp_set:N = \g__examzh_sealline_even_circle_end_fp,
    circle-end .code:n = 
      {
        \fp_gset:Nn \g__examzh_sealline_odd_circle_end_fp {#1}
        \fp_gset:Nn \g__examzh_sealline_even_circle_end_fp {#1}
      },
    % --步长--
    odd-circle-step .dim_set:N = \g__examzh_sealline_odd_circle_step_dim,
    even-circle-step .dim_set:N = \g__examzh_sealline_even_circle_step_dim,
    circle-step .code:n = 
      {
        \dim_gset:Nn \g__examzh_sealline_odd_circle_step_dim {#1}
        \dim_gset:Nn \g__examzh_sealline_even_circle_step_dim {#1}
      },
    % --直径--
    odd-circle-diameter .dim_set:N = \g__examzh_sealline_odd_circle_diameter_dim,
    even-circle-diameter .dim_set:N = \g__examzh_sealline_even_circle_diameter_dim,
    circle-diameter .code:n = 
      {
        \dim_gset:Nn \g__examzh_sealline_odd_circle_diameter_dim {#1}
        \dim_gset:Nn \g__examzh_sealline_even_circle_diameter_dim {#1}
      },
    % --偏移--
    odd-circle-xshift .dim_set:N = \g__examzh_sealline_odd_circle_xshift_dim,
    even-circle-xshift .dim_set:N = \g__examzh_sealline_even_circle_xshift_dim,
    circle-xshift .code:n = 
      {
        \dim_gset:Nn \g__examzh_sealline_odd_circle_xshift_dim {#1}
        \dim_gset:Nn \g__examzh_sealline_even_circle_xshift_dim {#1}
      },
    % 学生信息
    % --内容--
    odd-info-content .clist_set:N = \g__examzh_sealline_odd_info_content_clist,
    % --分隔符--
    odd-info-seperator .tl_set:N = \g__examzh_sealline_odd_info_seperator_tl,
    % --对齐--
    odd-info-align .tl_set:N = \g__examzh_sealline_odd_info_align_tl,
    % --偏移--
    odd-info-xshift .dim_set:N = \g__examzh_sealline_odd_info_xshift_dim,
    odd-info-yshift .dim_set:N = \g__examzh_sealline_odd_info_yshift_dim,
  }
\keys_set:nn { exam-zh / sealline }
  {
    show        = false,
    % scope      = firstpage,
    % scope      = oddpage,
    scope        = everypage,
    line-thickness          = 1pt,
    line-xshift             = 8mm,
    line-yshift             = 0mm,
    line-type               = loosely-dashed,
    text                    = 密封线内不得答题,
    text-xshift             = 11mm,
    text-yshift             = 0pt,
    text-width              = 0.8\textheight,
    text-format             = \zihao{4}\sffamily\color{black},
    text-xscale             = 1.0,
    text-yscale             = 0.8,
    text-direction-vertical = false,
    circle-show             = true,
    circle-start            = 0.07,
    circle-end              = 0.92,
    circle-step             = 3.5em,
    circle-diameter         = 3mm,
    circle-xshift           = 8mm,
    odd-info-content        = {
      {\kaishu 姓名}：{\underline{\hspace*{8em}}},
      {\kaishu 准考证号}：{\underline{\hspace*{8em}}},
      {\kaishu 考场号}：{\underline{\hspace*{8em}}},
      {\kaishu 座位号}：{\underline{\hspace*{8em}}}
    },
    odd-info-seperator   = \hspace*{3em},
    odd-info-align       = center,
    odd-info-xshift      = 20mm,
    odd-info-yshift      = 0mm
  }
\keys_define:nn { exam-zh }
  { sealline .meta:nn = { exam-zh / sealline } {#1} }

% 只在第一页出现
\cs_new:Npn \__examzh_sealline_scope_firstpage:
  {
    \AddToHook { shipout / firstpage } [ sealline ]
      {
        \put (0cm, 0cm)
          { \color{black} \__examzh_sealline_odd: }
      }
  }
% 只在第一页和最后一页出现
\cs_new:Npn \__examzh_sealline_scope_firstpage_and_lastpage:
  {
    \AddToHook { shipout / firstpage } [ sealline ]
      {
        \put (0cm, 0cm)
          { \color{black} \__examzh_sealline_odd: }
      }
    \AddToHook { shipout / lastpage } [ sealline ]
      {
        \put (0cm, 0cm)
          {
            \color{black} 
            \int_if_odd:nTF { \c@page }
              { \__examzh_sealline_odd: }
              { \__examzh_sealline_even: }
          }
      }
  }
% 只在奇数页出现
\cs_new:Npn \__examzh_sealline_scope_oddpage:
  {
    \AddToHook { shipout / background } [ sealline ]
      {
        \put (0cm, 0cm)
          {
            \color{black} 
            \int_if_odd:nT { \c@page }
              { \__examzh_sealline_odd: }
          }
      }
  }
% 每页都有，奇偶不同
\cs_new:Npn \__examzh_sealline_scope_everypage:
  {
    \AddToHook { shipout / background } [ sealline ]
      {
        \put (0cm, 0cm)
          {
            \color{black} 
            \int_if_odd:nTF { \c@page }
              { \__examzh_sealline_odd: }
              { \__examzh_sealline_even: }
          }
      }
  }
% mod 2 = 1 的类型
\cs_new:cpn { __examzh_sealline_scope_mod_2_equals_1: }
  {
    \AddToHook { shipout / background } [ sealline ]
      {
        \put (0cm, 0cm)
          {
            \color{black} 
            \int_compare:nNnT { \int_mod:nn { \c@page } { 2 } } = {1} { \__examzh_sealline_odd: }
          }
      }
  }
% mod 3 = 1 的类型
\cs_new:cpn { __examzh_sealline_scope_mod_3_equals_1: }
  {
    \AddToHook { shipout / background } [ sealline ]
      {
        \put (0cm, 0cm)
          {
            \color{black} 
            \int_compare:nNnT { \int_mod:nn { \c@page } { 3 } } = {1} { \__examzh_sealline_odd: }
          }
      }
  }
% mod 4 = 1 的类型
\cs_new:cpn { __examzh_sealline_scope_mod_4_equals_1: }
  {
    \AddToHook { shipout / background } [ sealline ]
      {
        \put (0cm, 0cm)
          {
            \color{black} 
            \int_compare:nNnT { \int_mod:nn { \c@page } { 4 } } = {1} { \__examzh_sealline_odd: }
          }
      }
  }
% mod 6 = 1 的类型
\cs_new:cpn { __examzh_sealline_scope_mod_6_equals_1: }
  {
    \AddToHook { shipout / background } [ sealline ]
      {
        \put (0cm, 0cm)
          {
            \color{black} 
            \int_compare:nNnT { \int_mod:nn { \c@page } { 6 } } = {1} { \__examzh_sealline_odd: }
          }
      }
  }

% https://github.com/CTeX-org/ctex-kit/issues/632#issuecomment-1199675064
\AddToHook{shipout/before}{\xeCJKShipoutHook}

\AtBeginDocument
  { \__examzh_sealline_set: }
\cs_new:Npn \__examzh_sealline_set:
  {
    \bool_if:NT \g__examzh_sealline_show_bool
      {
        \__examzh_sealline_scope_set:
        \str_case:Vn \g__examzh_sealline_scope_str
          {
            { firstpage } { \__examzh_sealline_scope_firstpage: }
            { oddpage   } { \__examzh_sealline_scope_oddpage:   }
            { everypage } { \__examzh_sealline_scope_everypage: }
            { first-and-last } { \__examzh_sealline_scope_firstpage_and_lastpage: }
            { mod-2 } { \use:c { __examzh_sealline_scope_mod_2_equals_1: } }
            { mod-3 } { \use:c { __examzh_sealline_scope_mod_3_equals_1: } }
            { mod-4 } { \use:c { __examzh_sealline_scope_mod_4_equals_1: } }
            { mod-6 } { \use:c { __examzh_sealline_scope_mod_6_equals_1: } }
          }
      }
  }
\cs_new:Npn \__examzh_sealline_scope_set:
  {
    \tl_gset:Nx \g__examzh_sealline_odd_type_parameter_tl
      {
        \str_case:Vn \g__examzh_sealline_odd_type_str
          {
            { solid }  { solid }
            { dotted } { dotted }
            { densely-dotted } { densely~dotted }
            { loosely-dotted } { loosely~dotted }
            { dashed } { dashed }
            { densely-dashed } { densely~dashed }
            { loosely-dashed } { loosely~dashed }
            { dash-dot } { dash~dot }
            { densely-dash-dot } { densely~dash~dot }
            { loosely-dash-dot } { loosely~dash~dot }
            { dash-dot-dot } { dash~dot~dot }
            { densely-dash-dot-dot } { densely~dash~dot~dot }
            { loosely-dash-dot-dot } { loosely~dash~dot~dot }
          }
      }
    \tl_gset:Nx \g__examzh_sealline_even_type_parameter_tl
      {
        \str_case:Vn \g__examzh_sealline_even_type_str
          {
            { solid }  { solid }
            { dotted } { dotted }
            { densely-dotted } { densely~dotted }
            { loosely-dotted } { loosely~dotted }
            { dashed } { dashed }
            { densely-dashed } { densely~dashed }
            { loosely-dashed } { loosely~dashed }
            { dash-dot } { dash~dot }
            { densely-dash-dot } { densely~dash~dot }
            { loosely-dash-dot } { loosely~dash~dot }
            { dash-dot-dot } { dash~dot~dot }
            { densely-dash-dot-dot } { densely~dash~dot~dot }
            { loosely-dash-dot-dot } { loosely~dash~dot~dot }
          }
      }
  }

\keys_define:nn { exam-zh / page }
  {
    show-columnline .bool_set:N = \l__examzh_show_columnline_bool,
    columnline-width .dim_set:N = \l__examzh_columnline_dim,
  }
\keys_set:nn { exam-zh / page }
  {
    show-columnline  = false,
    columnline-width = 0.4pt
  }

\AtEndPreamble
  {
    \bool_if:NTF \g__examzh_page_size_custom_bool
      {
        \dim_compare:nNnTF { \g__examzh_page_paper_width_dim } > { 0pt }
          {
            \dim_compare:nNnTF { \g__examzh_page_paper_height_dim } > { 0pt }
              {
                % 自定义纸张始终是单页模式；不继承 a3paper 的横向双页拼版设置。
                \geometry
                  {
                    paperwidth  = \g__examzh_page_paper_width_dim,
                    paperheight = \g__examzh_page_paper_height_dim,
                    margin      = \g__examzh_page_margin_dim,
                    headheight  = \g__examzh_page_headheight_dim
                  }
              }
              { \msg_error:nn { exam-zh } { custom-paper-height-required } }
          }
          { \msg_error:nn { exam-zh } { custom-paper-width-required } }
      }
      {
        \bool_if:cTF { g__examzh_page_size_a4paper_bool }
      {
        % a4paper
        \bool_if:NTF \g__examzh_sealline_show_bool
          {
            % 有密封线
            \geometry
              {
                twoside,
                paper  = a4paper,
                margin = 1in,
                inner  = 1.3in,
                outer  = 0.8in,
                headheight  = 0.7in
              }
          }
          {
            % 无密封线
            \geometry
              {
                paper  = a4paper,
                margin = 1in,
                headheight  = 0.7in
              }
          }
      }
      {
        % a3paper
        \bool_if:NT \l__examzh_show_columnline_bool
          { \dim_set:Nn \columnseprule { \l__examzh_columnline_dim } }
        \bool_if:NTF \g__examzh_sealline_show_bool
          {
            % 有密封线
            \geometry
              {
                twoside,
                paper      = a3paper,
                landscape,
                twocolumn,
                columnsep  = 30mm,
                margin     = 1in,
                inner      = 1.2in,
                outer      = 0.8in,
                headheight  = 0.7in
                % showframe
              }
          }
          {
            % 无密封线
            \geometry
              {
                paper      = a3paper,
                landscape,
                twocolumn,
                columnsep  = 30mm,
                margin     = 1in,
                headheight  = 0.7in
              }
          }
      }
      }
  }


\cs_new:Npn \__examzh_sealline_odd:
  {
    \begin{tikzpicture}
      [
        remember~picture,
        overlay
      ]
      % 密封线：线
      \__examzh_sealline_odd_line:
      % 密封线：小圆圈
      \__examzh_sealline_odd_circle:
      % 线上的文字
      \__examzh_sealline_odd_text_around_line:
      % 学生信息
      \__examzh_sealline_odd_infomation:
    \end{tikzpicture}
  }

\cs_new:Npn \__examzh_sealline_even:
  {
    \begin{tikzpicture}[remember~picture, overlay]
      % 密封线：线
      \__examzh_sealline_even_line:
      % 密封线：小圆圈
      \__examzh_sealline_even_circle:
      % 线上的文字
      \__examzh_sealline_even_text_around_line:
    \end{tikzpicture}
  }

% 线
\cs_new:Npn \__examzh_sealline_odd_line:
  {
    \use:x
      {
        \exp_not:N \draw
          [
            \g__examzh_sealline_odd_type_parameter_tl,
            line~width   = \dim_use:N \g__examzh_sealline_odd_line_thickness_dim
          ]
      }
      ([xshift = -\g__examzh_sealline_odd_line_xshift_dim, yshift = -\g__examzh_sealline_odd_line_yshift_dim]current~page~text~area.north~west)
        --
      ([xshift = -\g__examzh_sealline_odd_line_xshift_dim, yshift = \g__examzh_sealline_odd_line_yshift_dim]current~page~text~area.south~west);
  }
\cs_new:Npn \__examzh_sealline_even_line:
  {
    \use:x
      {
        \exp_not:N \draw
          [
            \g__examzh_sealline_even_type_parameter_tl,
            line~width    = \dim_use:N \g__examzh_sealline_even_line_thickness_dim
          ] 
      }
      ([xshift = \g__examzh_sealline_even_line_xshift_dim, yshift = -\g__examzh_sealline_even_line_yshift_dim]current~page~text~area.north~east)
        --
      ([xshift = \g__examzh_sealline_even_line_xshift_dim, yshift = \g__examzh_sealline_even_line_yshift_dim]current~page~text~area.south~east);
  }

% 小圆圈
\cs_new:Npn \__examzh_sealline_odd_circle:
  {
    \bool_if:NT \g__examzh_sealline_odd_circle_show_bool
      {
        \use:x
          {
            \exp_not:N
            \fill
              [
                decorate,
                decoration =
                  {
                    markings,
                    mark = 
                    between~positions~
                    \fp_use:N \g__examzh_sealline_odd_circle_start_fp
                    ~and~
                    \fp_use:N \g__examzh_sealline_odd_circle_end_fp
                    ~step~
                    \dim_use:N \g__examzh_sealline_odd_circle_step_dim
                    ~with
                      {
                        \exp_not:N
                        \node 
                          [
                            circle,
                            draw         = black, 
                            fill         = white,
                            minimum~size = \dim_use:N \g__examzh_sealline_odd_circle_diameter_dim
                          ]
                          {};
                      }
                  }
              ]
          }
        ([xshift = -\g__examzh_sealline_odd_circle_xshift_dim]current~page~text~area.north~west)
          --
        ([xshift = -\g__examzh_sealline_odd_circle_xshift_dim]current~page~text~area.south~west);
      }
  }
\cs_new:Npn \__examzh_sealline_even_circle:
  {
    \bool_if:NT \g__examzh_sealline_even_circle_show_bool
      {
        \use:x
          {
            \exp_not:N
            \fill
              [
                decorate,
                decoration =
                  {
                    markings,
                    mark = 
                    between~positions~
                    \fp_use:N \g__examzh_sealline_even_circle_start_fp
                    ~and~
                    \fp_use:N \g__examzh_sealline_even_circle_end_fp
                    ~step~
                    \dim_use:N \g__examzh_sealline_even_circle_step_dim
                    ~with
                      {
                        \exp_not:N
                        \node 
                          [
                            circle,
                            draw         = black, 
                            fill         = white,
                            minimum~size = \dim_use:N \g__examzh_sealline_even_circle_diameter_dim
                          ]
                          {};
                      }
                  }
              ]
          }
        ([xshift = \g__examzh_sealline_even_circle_xshift_dim]current~page~text~area.north~east)
          --
        ([xshift = \g__examzh_sealline_even_circle_xshift_dim]current~page~text~area.south~east);
      }
  }
% 线上的文字（密封线外不得答题）
\cs_new_protected:Npn \__examzh_sealline_odd_spread_box:nn #1#2
  {
    \mode_leave_vertical:
    \bool_if:NTF \g__examzh_sealline_odd_text_direction_vertical_bool
      {
        \hbox_set_to_wd:Nnn \l_tmpa_box {#1}
          {
            \tl_set:Nx \l_tmpa_tl {#2}
            \tl_reverse:N \l_tmpa_tl
            \tl_map_inline:xn { \l_tmpa_tl }
              {
                \hbox_set:Nn \l_tmpb_box {##1}
                \box_rotate:Nn \l_tmpb_box { -90 }
                \box_use_drop:N \l_tmpb_box
                \hfil 
              }
            \unskip
          }
      }
      {
        \hbox_set_to_wd:Nnn \l_tmpa_box {#1}
          {
            \tl_map_inline:xn {#2} { ##1 \hfil } \unskip
          }
      }
    \box_scale:Nnn \l_tmpa_box
      { \fp_use:N \g__examzh_sealline_odd_text_xscale_fp }
      { \fp_use:N \g__examzh_sealline_odd_text_yscale_fp }
    \box_rotate:Nn \l_tmpa_box { 90 }
    \box_move_down:nn 
      { #1 / 2 }
      { \box_use_drop:N \l_tmpa_box }
  }
\cs_new_protected:Npn \__examzh_sealline_even_spread_box:nn #1#2
  {
    \mode_leave_vertical:
    \bool_if:NTF \g__examzh_sealline_odd_text_direction_vertical_bool
      {
        \hbox_set_to_wd:Nnn \l_tmpa_box {#1}
          {
            \tl_map_inline:xn {#2}
              {
                \hbox_set:Nn \l_tmpb_box {##1}
                \box_rotate:Nn \l_tmpb_box { 90 }
                \box_use_drop:N \l_tmpb_box
                \hfil 
              }
            \unskip
          }
      }
      {
        \hbox_set_to_wd:Nnn \l_tmpa_box {#1}
          {
            \tl_map_inline:xn {#2} { ##1 \hfil } \unskip
          }
      }
    \box_scale:Nnn \l_tmpa_box
      { \fp_use:N \g__examzh_sealline_odd_text_xscale_fp }
      { \fp_use:N \g__examzh_sealline_odd_text_yscale_fp }
    \box_rotate:Nn \l_tmpa_box { -90 }
    \box_move_up:nn 
      { #1 / 2 }
      { \box_use_drop:N \l_tmpa_box }
  }
\cs_new:Npn \__examzh_sealline_odd_text_around_line:
  {
    \node [ anchor = east ]
      at ([xshift = -\g__examzh_sealline_odd_text_xshift_dim, yshift = \g__examzh_sealline_odd_text_yshift_dim]current~page~text~area.west)
      {
        \g__examzh_sealline_odd_text_format_tl
        \__examzh_sealline_odd_spread_box:nn { \g__examzh_sealline_odd_text_width_dim }
          { \g__examzh_sealline_odd_text_tl }
      };
  }
\cs_new:Npn \__examzh_sealline_even_text_around_line:
  {
    \bool_if:NTF \g__examzh_sealline_odd_text_direction_vertical_bool
      {
        \dim_set_eq:NN \l_tmpa_dim \g__examzh_sealline_even_text_yshift_dim
      }
      {
        \dim_set:Nn \l_tmpa_dim { - \g__examzh_sealline_even_text_yshift_dim }
      }
    \dim_set_eq:NN \g__examzh_sealline_even_text_yshift_dim \l_tmpa_dim
    \node [ anchor = west ]
      at ([xshift = \g__examzh_sealline_even_text_xshift_dim, yshift = \g__examzh_sealline_even_text_yshift_dim]current~page~text~area.east)
      {
        \g__examzh_sealline_even_text_format_tl
        \__examzh_sealline_even_spread_box:nn { \g__examzh_sealline_even_text_width_dim }
          { \g__examzh_sealline_even_text_tl }
      };
  }
% 学生信息
\cs_new:Npn \__examzh_sealline_odd_infomation:
  {
    \use:x 
      {
        \exp_not:N
        \path
          [
            decorate,
            decoration =
              {
                text~along~path,
                text~align = \g__examzh_sealline_odd_info_align_tl,
                reverse~path,
                text = {
                  \clist_use:Nn \g__examzh_sealline_odd_info_content_clist 
                    { { \g__examzh_sealline_odd_info_seperator_tl } }
                }
              }
          ]
      }
      ([xshift = -\g__examzh_sealline_odd_info_xshift_dim, yshift = 0mm]current~page~text~area.north~west)
        --
      ([xshift = -\g__examzh_sealline_odd_info_xshift_dim, yshift = \g__examzh_sealline_odd_info_yshift_dim]current~page~text~area.south~west);
  }

% 信息的方格
\keys_define:nn { exam-zh / square }
  {
    x-length .dim_set:N = \l__examzh_information_square_x_dim,
    y-length .dim_set:N = \l__examzh_information_square_y_dim,
    baseline .dim_set:N = \l__examzh_information_square_baseline_dim,
    linewidth .dim_set:N = \l__examzh_information_square_linewidth_dim,
    xshift .dim_set:N = \l__examzh_information_square_xshift_dim
  }
\keys_set:nn { exam-zh / square }
  {
    x-length  = 1.4em,
    y-length  = 1.2em,
    baseline  = 3pt,
    linewidth = 0.4pt,
  }
\keys_define:nn { exam-zh }
  { square .meta:nn = { exam-zh / square } {#1} }
\cs_new:Npn \__examzh_information_square_single:
  {
    \begin{tikzpicture}[baseline = \l__examzh_information_square_baseline_dim]
      \draw[line~width = \l__examzh_information_square_linewidth_dim] (0,0) rectangle 
        ( \l__examzh_information_square_x_dim , \l__examzh_information_square_y_dim);
    \end{tikzpicture}
  }
\cs_new:Npn \__examzh_information_square_multiple:n #1
  {
    \dim_compare:nNnT { \l__examzh_information_square_xshift_dim } = { 0pt }
      {
        \dim_set_eq:NN 
          \l__examzh_information_square_xshift_dim
          \l__examzh_information_square_linewidth_dim
      }
    \int_compare:nNnTF { #1 } = { 1 }
      { \__examzh_information_square_single: }
      {
        \__examzh_information_square_single:
        \prg_replicate:nn { #1 - 1 }
          {
            \hspace*{ -\l__examzh_information_square_xshift_dim }
            \__examzh_information_square_single:
          }
      }
  }

\NewDocumentCommand { \examsquare } { O{ } m }
  {
    \group_begin:
      \keys_set:nn { exam-zh / square } { #1 }
      \__examzh_information_square_multiple:n { #2 }
    \group_end:
  }


% 页眉和页脚


\keys_define:nn { exam-zh / page }
  {
    show-head .bool_set:N = \l__examzh_show_head_bool,
    show-foot .bool_set:N = \l__examzh_show_foot_bool,
    head-content .tl_set:N = \l__examzh_head_content_tl,
    foot-content .tl_set:N = \l__examzh_foot_content_format_tl
      % foo, bar: foo <page> bar
      % foo, bar, baz: foo <page> bar <lastpage> baz
  }
\keys_set:nn { exam-zh / page }
  {
    show-head    = false,
    show-foot    = true,
    foot-content = {数学试题第;页（共;页）}
  }

\int_new:N \l__examzh_foot_content_count_semicolon_int
\tl_new:N \l__examzh_foot_content_before_page_tl
\tl_new:N \l__examzh_foot_content_after_page_tl
\tl_new:N \l__examzh_foot_content_after_lastpage_tl
\cs_generate_variant:Nn \regex_count:nnN { nxN, noN }

\cs_new:Npn \__examzh_foot_content_only_page_input:ww #1 ; #2 \q_stop
  {
    \tl_set:Nn \l__examzh_foot_content_before_page_tl {#1}
    \tl_set:Nn \l__examzh_foot_content_after_page_tl {#2}
  }
\cs_new:Npn \__examzh_foot_content_only_page_input:n #1
  {
    \__examzh_foot_content_only_page_input:ww #1 \q_stop
  }
\cs_generate_variant:Nn \__examzh_foot_content_only_page_input:n { V }

\cs_new:Npn \__examzh_foot_content_page_and_lastpage_input:www #1 ; #2 ; #3 \q_stop
  {
    \tl_set:Nn \l__examzh_foot_content_before_page_tl {#1}
    \tl_set:Nn \l__examzh_foot_content_after_page_tl {#2}
    \tl_set:Nn \l__examzh_foot_content_after_lastpage_tl {#3}
  }
\cs_new:Npn \__examzh_foot_content_page_and_lastpage_input:n #1
  {
    \__examzh_foot_content_page_and_lastpage_input:www #1 \q_stop
  }
\cs_generate_variant:Nn \__examzh_foot_content_page_and_lastpage_input:n { V }

\cs_new:Npn \__examzh_foot_content_no_page_or_lastpage_output:
  {
    \l__examzh_foot_content_format_tl
  }
\cs_new:Npn \__examzh_foot_content_only_page_output:
  {
    \l__examzh_foot_content_before_page_tl \thepage { }
    \l__examzh_foot_content_after_page_tl
  }
\cs_new:cpn { __examzh_foot_content_only_page_a3paper_left_output: }
  {
    \l__examzh_foot_content_before_page_tl \int_eval:n { 2 * \c@page - 1 } { }
    \l__examzh_foot_content_after_page_tl
  }
\cs_new:cpn { __examzh_foot_content_only_page_a3paper_right_output: }
  {
    \l__examzh_foot_content_before_page_tl \int_eval:n { 2 * \c@page } { }
    \l__examzh_foot_content_after_page_tl
  }
\cs_new:Npn \__examzh_foot_content_page_and_lastpage_output:
  {
    \l__examzh_foot_content_before_page_tl \thepage { }
    \l__examzh_foot_content_after_page_tl  \unskip \c_space_tl \pageref { LastPage }~
    \l__examzh_foot_content_after_lastpage_tl
  }
\cs_new:cpn { __examzh_foot_content_page_and_lastpage_a3paper_left_output: }
  {
    \__examzh_foot_lastpage_tmp_set:
    \l__examzh_foot_content_before_page_tl \int_eval:n { 2 * \c@page - 1 } { }
    \l__examzh_foot_content_after_page_tl  ~\int_eval:n { 2 * \l__examzh_tmp_int }~
    \l__examzh_foot_content_after_lastpage_tl
  }
\cs_new:cpn { __examzh_foot_content_page_and_lastpage_a3paper_right_output: }
  {
    \__examzh_foot_lastpage_tmp_set:
    \l__examzh_foot_content_before_page_tl \int_eval:n { 2 * \c@page } { }
    \l__examzh_foot_content_after_page_tl  ~\int_eval:n { 2 * \l__examzh_tmp_int }~
    \l__examzh_foot_content_after_lastpage_tl
  }
\cs_new_nopar:Npn \__examzh_relax: { \relax }
% \cs_new:Npn \__examzh_foot_lastpage_tmp_set:
%   {
%     \cs_if_eq:NNTF \lastpage@lastpage \__examzh_relax:
%       { \int_set:Nn \l__examzh_tmp_int { 0 } }
%       { \int_set:Nn \l__examzh_tmp_int { \lastpage@lastpage } }
%   }

% https://gitee.com/xkwxdyy/exam-zh/issues/I6B7MU#note_17945353_link
\cs_new:Npn \__examzh_foot_lastpage_tmp_set:
  {
    \bool_lazy_or:nnTF 
      { \cs_if_eq_p:NN \lastpage@lastpage \__examzh_relax: }
      { \str_if_eq_p:ee \lastpage@lastpage {??} }
      { \int_set:Nn \l__examzh_tmp_int { 0 } }
      { \int_set:Nn \l__examzh_tmp_int { \lastpage@lastpage } }
  }
\msg_new:nnn { exam-zh } { foot-semicolon-number-error }
  {
    The~number~of~semicolon~of~foot-line~must~be~1,~2~or~3!
  }
\cs_new:cpn { __examzh_foot_content_a4paper_output: }
  {
    % 检测输入有多少个 ;
    % \regex_count:nxN { ; }
    % https://gitee.com/xkwxdyy/exam-zh/issues/I5NNR8#note_13447684_link
    \regex_count:noN { ; } 
      { \l__examzh_foot_content_format_tl }
      \l__examzh_foot_content_count_semicolon_int
    \int_case:nnF { \l__examzh_foot_content_count_semicolon_int }
      {
        {0} { \__examzh_foot_content_no_page_or_lastpage_output: }
        {1}
          { 
            % 将输入“分解”
            \__examzh_foot_content_only_page_input:V \l__examzh_foot_content_format_tl
            % 然后输出
            \__examzh_foot_content_only_page_output:
          }
        {2}
          {
            % 将输入“分解”
            \__examzh_foot_content_page_and_lastpage_input:V \l__examzh_foot_content_format_tl
            % 然后输出
            \__examzh_foot_content_page_and_lastpage_output:
          }
      }
      {
        \msg_error:nn { exam-zh } { foot-semicolon-number-error }
      }
  }
\cs_set_eq:cc
  { __examzh_foot_content_a3paper_common_output: }
  { __examzh_foot_content_a4paper_output: }
\cs_new:cpn { __examzh_foot_content_a3paper_separate_left_output: }
  {
    % 检测输入有多少个 ;
    \regex_count:nxN { ; } 
      { \l__examzh_foot_content_format_tl }
      \l__examzh_foot_content_count_semicolon_int
    \int_case:nnF { \l__examzh_foot_content_count_semicolon_int }
      {
        {0} { \__examzh_foot_content_no_page_or_lastpage_output: }
        {1}
          { 
            % 将输入“分解”
            \__examzh_foot_content_only_page_input:V \l__examzh_foot_content_format_tl
            % 然后输出
            \use:c { __examzh_foot_content_only_page_a3paper_left_output: }
          }
        {2}
          {
            % 将输入“分解”
            \__examzh_foot_content_page_and_lastpage_input:V \l__examzh_foot_content_format_tl
            % 然后输出
            \use:c { __examzh_foot_content_page_and_lastpage_a3paper_left_output: }
          }
      }
      {
        \msg_error:nn { exam-zh } { foot-semicolon-number-error }
      }
  }
\cs_new:cpn { __examzh_foot_content_a3paper_separate_right_output: }
  {
    % 检测输入有多少个 ;
    \regex_count:nxN { ; } 
      { \l__examzh_foot_content_format_tl }
      \l__examzh_foot_content_count_semicolon_int
    \int_case:nnF { \l__examzh_foot_content_count_semicolon_int }
      {
        {0} { \__examzh_foot_content_no_page_or_lastpage_output: }
        {1}
          { 
            % 将输入“分解”
            \__examzh_foot_content_only_page_input:V \l__examzh_foot_content_format_tl
            % 然后输出
            \use:c { __examzh_foot_content_only_page_a3paper_right_output: }
          }
        {2}
          {
            % 将输入“分解”
            \__examzh_foot_content_page_and_lastpage_input:V \l__examzh_foot_content_format_tl
            % 然后输出
            \use:c { __examzh_foot_content_page_and_lastpage_a3paper_right_output: }
          }
      }
      {
        \msg_error:nn { exam-zh } { foot-semicolon-number-error }
      }
  }

\tl_set:Nn \headrulewidth { 0pt }
% \cs_set_eq:NN \@mkboth \use_none:n
\cs_set_eq:NN \sectionmark \use_none:n
\cs_set_eq:NN \subsectionmark \use_none:n

\cs_new:Npn \__examzh_column_box:n #1
  {
    \makebox [ \columnwidth ] {#1}
  }


\AtEndPreamble
  {
    \fancypagestyle { plain }
      {
        \fancyhf { }
        \bool_if:NT \l__examzh_show_head_bool
          {
            \l__examzh_head_content_tl
          }
        \bool_if:cTF { g__examzh_page_size_a4paper_bool }
          {
            % a4paper
            \bool_if:NT \l__examzh_show_foot_bool
              {
                \fancyfoot [ C ]
                  {
                    \small
                    \use:c { __examzh_foot_content_a4paper_output: }
                    % \l__examzh_subject_tl 试题第 \thepage { } 页（共 \pageref { LastPage } ~ 页）
                  }
              }
          }
          {
            % a3paper
            \bool_if:NT \l__examzh_show_foot_bool
              {
                \bool_if:cTF 
                  { g__examzh_page_a3paper_foot_common_bool }
                  {
                    % 两页共用一个页脚
                    \fancyfoot [ C ]
                      {
                        \small
                        % \l__examzh_subject_tl 试题第 \thepage { } 页（共 \pageref { LastPage } ~ 页）
                        \use:c { __examzh_foot_content_a3paper_common_output: }
                      }
                  }
                  {
                    % 每页一个页脚
                    \fancyfoot [ L ]
                      {
                        \__examzh_column_box:n
                          {
                            \small
                            \use:c { __examzh_foot_content_a3paper_separate_left_output: }
                            % \l__examzh_subject_tl 试题第 
                            % % \thepage 
                            % \int_eval:n { 2 * \c@page - 1 }
                            % { } 页
                            % （共 
                            % % \pageref { LastPage } 
                            % \int_eval:n { 2 * \l__examzh_tmp_int }
                            % ~ 页）
                            % \use:c { l__examzh_foot_content_a3paper_left_tl }
                          }
                      }
                    \fancyfoot [ R ]
                      {
                        \__examzh_column_box:n
                          {
                            \small
                            \use:c { __examzh_foot_content_a3paper_separate_right_output: }
                            % % \int_gincr:N \c@page
                            % \int_set:Nn \l__examzh_tmp_int { \lastpage@lastpage }
                            
                            % \l__examzh_subject_tl 试题第 
                            % \int_eval:n { 2 * \c@page }
                            % { } 
                            % 页
                            % （共 
                            % % \pageref { LastPage }
                            % \int_eval:n { 2 * \l__examzh_tmp_int }
                            % ~ 页）
                            % % \use:c { l__examzh_foot_content_a3paper_right_tl }
                          }
                      }
                  }
              }
          }
      }
  }
\AtBeginDocument { \pagestyle { plain } }



% 草稿纸

% “草稿纸”三个字的尺寸
\dim_new:N \l__examzh_draft_watermark_size_dim
% 控制是否自动添加草稿纸
\bool_new:N \l__examzh_draft_show_auto_bool

\keys_define:nn { exam-zh / draft }
  {
    watermark-size .code:n = 
      {
        % 之所以这样设置而不是 .dim_set:N 是因为 a4 和 a3 的切换不是文档选项
        % 而是通过 \examsetup 更改的。所以要设置默认值必须用 \AtEndPreamble
        % 来检测 g__examzh_page_size_a4paper_bool 的值才行，但这样的话就会
        % 把 watermark-size 的值覆盖，所以得实现“watermark-size” 的设置要盖掉 \AtEndPreamble
        % 设置的默认值，才可以达到用户输入产生效果
        \AtEndPreamble
          {
            \dim_set:Nn \l__examzh_draft_watermark_size_dim {#1}
          }
      },
    show-watermark .bool_set:N = \l__examzh_draft_show_watermark_bool,
    show-draft .choice:,
    show-draft / auto .code:n = 
      { \bool_set_true:N \l__examzh_draft_show_auto_bool },    
    show-draft / manual .code:n = 
      { \bool_set_false:N \l__examzh_draft_show_auto_bool },
  }
\keys_set:nn { exam-zh / draft }
  {
    show-watermark = true,
    show-draft     = manual,
  }
\keys_define:nn { exam-zh }
{
    draft .meta:nn = { exam-zh / draft } {#1} 
}
% “草稿纸”尺寸默认值
\AtEndPreamble
  {
    \bool_if:cTF { g__examzh_page_size_a4paper_bool }
      {
        \keys_set:nn { exam-zh / draft }
          { watermark-size = 100pt }
      }
      {
        \keys_set:nn { exam-zh / draft }
          { watermark-size = 180pt }
      }
  }
\AtEndDocument
  {
    \bool_if:NT \l__examzh_draft_show_auto_bool
      { \draftpaper \draftpaper }
  }
\NewDocumentCommand { \draftpaper } { O{} }
  {
    \group_begin:
      \keys_set:nn { exam-zh / draft } {#1}
      \bool_if:NTF \l__examzh_draft_show_watermark_bool
        { \__examzh_draft_with_watermark: }
        { \__examzh_draft_without_watermark: }
    \group_end:
  }
\cs_new:Npn \__examzh_draft_with_watermark:
  {
    \clearpage
    \null
    \thispagestyle { empty }
    \begin{tikzpicture}
        [
          remember~picture,
          overlay,
          font = \sffamily\fontsize{ \l__examzh_draft_watermark_size_dim }{180pt}\selectfont
        ]
      \node[text = lightgray!40] at (current~page.center) {草\quad 稿\quad 纸};
    \end{tikzpicture}
    \clearpage
  }
\cs_new:Npn \__examzh_draft_without_watermark:
  {
    \clearpage
    \null
    \thispagestyle { empty }
    \clearpage
  }



% 评分框 scoring box

\bool_new:N \g__examzh_combine_section_with_scoringbox_position_left_bool

\NewCommandCopy { \examzholdsection } { \section }
\cs_new:Npn \__examzh_combine_section_with_twocolumn_scoringbox:
  {
    \bool_if:NTF \g__examzh_combine_section_with_scoringbox_position_left_bool
      {
        \RenewDocumentCommand { \section } { m }
          {
            \par\addvspace{1.5em}\noindent
            \begin{tabular}{lc}
              \begin{minipage}{0.2\columnwidth}
                \begin{tabular}{|c|c|}
                  \hline 
                  得分 & \rule{3em}{0pt}\rule[-0.7em]{0pt}{2em} \\\hline
                  阅卷人 & \rule{3em}{0pt}\rule[-0.7em]{0pt}{2em} \\\hline
                \end{tabular}
              \end{minipage} & 
              \begin{minipage}{0.745\columnwidth}
                \examzholdsection {##1}
              \end{minipage}
            \end{tabular}
            \par
            \addvspace{1em}
          }
      }
      {
        \RenewDocumentCommand { \section } { m }
          {
            \par\addvspace{1.5em}\noindent
            \begin{tabular}{lc}
              \begin{minipage}{0.745\columnwidth}
                \examzholdsection {##1}
              \end{minipage} & 
              \begin{minipage}{0.2\columnwidth}
                \begin{tabular}{|c|c|}
                  \hline 
                  得分 & \rule{3em}{0pt}\rule[-0.7em]{0pt}{2em} \\\hline
                  阅卷人 & \rule{3em}{0pt}\rule[-0.7em]{0pt}{2em} \\\hline
                \end{tabular}
              \end{minipage}
            \end{tabular}
            \par
            \addvspace{1em}
          }
      }
  }
\cs_new:Npn \__examzh_combine_section_with_onecolumn_scoringbox:
  {
    \bool_if:NTF \g__examzh_combine_section_with_scoringbox_position_left_bool
      {
        \RenewDocumentCommand { \section } { m }
          {
            \par\addvspace{1.5em}\noindent
            \begin{tabular}{cc}
              \begin{varwidth}{3.5em}
                \begin{tabular}{|c|}
                  \hline 
                  得分\rule[-0.7em]{0pt}{2em} \\\hline
                  \rule[-0.7em]{0pt}{2em} \\\hline
                \end{tabular}
              \end{varwidth} & 
              \begin{varwidth}{0.865\columnwidth}
                \examzholdsection {##1}
              \end{varwidth}
            \end{tabular}
            \par
            \addvspace{1em}
          }
      }
      {
        \RenewDocumentCommand { \section } { m }
          {
            \par\addvspace{1.5em}\noindent
            \begin{tabular}{cc}
              \begin{varwidth}{0.865\columnwidth}
                \examzholdsection {##1}
              \end{varwidth} &
              \begin{varwidth}{3.5em}
                \begin{tabular}{|c|}
                  \hline 
                  得分\rule[-0.7em]{0pt}{2em} \\\hline
                  \rule[-0.7em]{0pt}{2em} \\\hline
                \end{tabular}
              \end{varwidth}
            \end{tabular}
            \par
            \addvspace{1em}
          }
      }
  }
\cs_new:Npn \__examzh_restore_section:
  {
    \RenewCommandCopy { \section } { \examzholdsection }
  }

\keys_define:nn { exam-zh / scoringbox }
  {
    type .choice:,
    type / onecolumn .code:n =
      {
        \AtEndPreamble { \__examzh_combine_section_with_onecolumn_scoringbox: }
      },
    type / twocolumn .code:n =
      {
        \AtEndPreamble { \__examzh_combine_section_with_twocolumn_scoringbox: }
      },
    type / none .code:n = { \__examzh_restore_section: },
    position .choice:,
    position / left .code:n =
      {
        \bool_gset_true:N \g__examzh_combine_section_with_scoringbox_position_left_bool
      },
    position / right .code:n =
      {
        \bool_gset_false:N \g__examzh_combine_section_with_scoringbox_position_left_bool
      },
  }
\keys_set:nn { exam-zh / scoringbox }
  {
    type = none,
    position = left
  }
\keys_define:nn { exam-zh }
  { scoringbox .meta:nn = { exam-zh / scoringbox } {#1} }


\NewDocumentCommand { \scoringbox } { s }
  {
    \IfBooleanTF {#1}
      { \__examzh_scoringbox_onecolumn: }
      { \__examzh_scoringbox_twocolumn: }
  }
\cs_new_protected:Nn \__examzh_scoringbox_twocolumn:
  {
    \begin{tabular}{|c|c|}
      \hline 
      得分 & \rule{3em}{0pt}\rule[-0.7em]{0pt}{2em} \\\hline
      阅卷人 & \rule{3em}{0pt}\rule[-0.7em]{0pt}{2em} \\\hline
    \end{tabular}
  }
\cs_new_protected:Nn \__examzh_scoringbox_onecolumn:
  {
    \begin{tabular}{|c|}
      \hline 
      得分\rule[-0.7em]{0pt}{2em} \\\hline
      \rule[-0.7em]{0pt}{2em} \\\hline
    \end{tabular}
  }



% 脚注
% 摘自 fduthesis.cls

\cs_new_protected:Npn \__examzh_define_fn_style:nn #1#2
  { \tl_const:cn { c__examzh_fn_style_ #1 _tl } {#2} }
\cs_new:Npn \__examzh_symbol:n #1 { \tex_char:D #1 \scan_stop: }

\clist_map_inline:nn
  {
    { plain           } { plain           },
    { libertinus      } { libertinus      },
    { libertinus_neg  } { libertinus*     },
    { libertinus_sans } { libertinus-sans },
    { pifont          } { pifont          },
    { pifont_neg      } { pifont*         },
    { pifont_sans     } { pifont-sans     },
    { pifont_sans_neg } { pifont-sans*    },
    { xits            } { xits            },
    { xits_sans       } { xits-sans       },
    { xits_sans_neg   } { xits-sans*      }
  }
  { \__examzh_define_fn_style:nn #1 }
\tl_new:N \l__examzh_fn_style_tl
\keys_define:nn { exam-zh / style }
  {
    footnote-style .choices:nn =
      {
        plain,
        libertinus, libertinus*, libertinus-sans,
        pifont,     pifont*,     pifont-sans,     pifont-sans*,
        xits,                    xits-sans,       xits-sans*
      }
      {
        \tl_gset_eq:NN \l__examzh_fn_style_tl \l_keys_choice_tl
        \int_compare:nT { 5 <= \l_keys_choice_int <= 8 }
          { \RequirePackage { pifont } }
      },
    footnote-style .value_required:n = true
  }
\keys_set:nn { exam-zh / style }
  {
    footnote-style = libertinus
  }
\cs_new:Npn \__examzh_fn_symbol_libertinus:n #1
  {
    \int_compare:nTF { #1 >= 21 }
      {
        \int_compare:nTF { #1 >= 47 }
          { \__examzh_symbol:n { \int_eval:n { "24B6 - 47 + #1 } } }
          { \__examzh_symbol:n { \int_eval:n { "24D0 - 21 + #1 } } }
      }
      { \__examzh_symbol:n { \int_eval:n { "2460 - 1 + #1 } } }
  }
\cs_new:Npn \__examzh_fn_symbol_libertinus_neg:n #1
  {
    \int_compare:nTF { #1 >= 11 }
      { \__examzh_symbol:n { \int_eval:n { "24EB - 11 + #1 } } }
      { \__examzh_symbol:n { \int_eval:n { "2776 -  1 + #1 } } }
  }
\cs_new_eq:NN \__examzh_fn_symbol_libertinus_sans:n \__examzh_fn_symbol_libertinus:n
\cs_new:Npn \__examzh_fn_symbol_pifont:n #1
  { \ding { \int_eval:n { 171 + #1 } } }
\cs_new:Npn \__examzh_fn_symbol_pifont_neg:n #1
  { \ding { \int_eval:n { 181 + #1 } } }
\cs_new:Npn \__examzh_fn_symbol_pifont_sans:n #1
  { \ding { \int_eval:n { 191 + #1 } } }
\cs_new:Npn \__examzh_fn_symbol_pifont_sans_neg:n #1
  { \ding { \int_eval:n { 201 + #1 } } }
\cs_new:Npn \__examzh_fn_symbol_xits:n #1
  {
    \int_compare:nTF { #1 >= 10 }
      {
        \int_compare:nTF { #1 >= 36 }
          { \__examzh_symbol:n { \int_eval:n { "24B6 - 36 + #1 } } }
          { \__examzh_symbol:n { \int_eval:n { "24D0 - 10 + #1 } } }
      }
      { \__examzh_symbol:n { \int_eval:n { "2460 - 1 + #1 } } }
  }
\cs_new:Npn \__examzh_fn_symbol_xits_sans:n #1
  { \__examzh_symbol:n { \int_eval:n { "2780 - 1 + #1 } } }
\cs_new:Npn \__examzh_fn_symbol_xits_sans_neg:n #1
  { \__examzh_symbol:n { \int_eval:n { "278A - 1 + #1 } } }
\cs_set:Npn \thefootnote { \examzh_footnote_number:N \c@footnote }
\cs_new:Npn \examzh_footnote_number:N #1
  {
    \tl_case:NnF \l__examzh_fn_style_tl
      {
        \c__examzh_fn_style_plain_tl
          { \int_use:N #1 }
        \c__examzh_fn_style_libertinus_tl
          {
            \fontspec { LibertinusSerif-Regular .otf }
            \__examzh_fn_symbol_libertinus:n {#1}
          }
        \c__examzh_fn_style_libertinus_neg_tl
          {
            \fontspec { LibertinusSerif-Regular .otf }
            \__examzh_fn_symbol_libertinus_neg:n {#1}
          }
        \c__examzh_fn_style_libertinus_sans_tl
          {
            \fontspec { LibertinusSans-Regular .otf }
            \__examzh_fn_symbol_libertinus_sans:n {#1}
          }
        \c__examzh_fn_style_pifont_tl
          { \__examzh_fn_symbol_pifont:n {#1} }
        \c__examzh_fn_style_pifont_neg_tl
          { \__examzh_fn_symbol_pifont_neg:n {#1} }
        \c__examzh_fn_style_pifont_sans_tl
          { \__examzh_fn_symbol_pifont_sans:n {#1} }
        \c__examzh_fn_style_pifont_sans_neg_tl
          { \__examzh_fn_symbol_pifont_sans_neg:n {#1} }
        \c__examzh_fn_style_xits_tl
          {
            \fontspec { XITS-Regular .otf }
            \__examzh_fn_symbol_xits:n {#1}
          }
        \c__examzh_fn_style_xits_sans_tl
          {
            \fontspec { XITS-Regular .otf }
            \__examzh_fn_symbol_xits_sans:n {#1}
          }
        \c__examzh_fn_style_xits_sans_neg_tl
          {
            \fontspec { XITS-Regular .otf }
            \__examzh_fn_symbol_xits_sans_neg:n {#1}
          }
      }
      { \int_use:N #1 }
  }
\cs_set:Npn \@makefntext #1
  {
    \mode_leave_vertical:
    \hbox_to_wd:nn { 1.5 em } { \@thefnmark \hfil }
    #1
  }
