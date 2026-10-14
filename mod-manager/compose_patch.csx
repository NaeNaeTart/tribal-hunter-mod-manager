using System;
using System.IO;
using System.Linq;
using System.Text.Json;
using UndertaleModLib.Models;
using UndertaleModLib.Compiler;
using UndertaleModLib.Decompiler;
var planPath=Environment.GetEnvironmentVariable("TRIBAL_PATCH_PLAN");
if(String.IsNullOrEmpty(planPath)) throw new Exception("Missing patch plan.");
var plan=JsonDocument.Parse(File.ReadAllText(planPath));
foreach(var package in plan.RootElement.GetProperty("packages").EnumerateArray()) {
 var root=package.GetString();
 string path(string rel) {
  var full=Path.GetFullPath(Path.Combine(root,rel));
  if(!full.StartsWith(Path.GetFullPath(root)+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase)) throw new Exception("Patch path escapes package.");
  return full;
 }
 var bundle=JsonDocument.Parse(File.ReadAllText(path("bundle.json"))).RootElement;
 if(bundle.GetProperty("format").GetInt32()!=1) throw new Exception("Unsupported patch format.");
 var shader=bundle.GetProperty("shader");var shaderName=shader.GetProperty("name").GetString();
 if(Data.Shaders.Any(s=>s.Name.Content==shaderName)) throw new Exception("Shader already installed: "+shaderName);
 var template=Data.Shaders[0];
 var asset=new UndertaleShader {Name=Data.Strings.MakeString(shaderName),Type=UndertaleShader.ShaderType.GLSL_ES,Version=2,
 GLSL_ES_Vertex=template.GLSL_ES_Vertex,GLSL_ES_Fragment=Data.Strings.MakeString(""),
 GLSL_Vertex=Data.Strings.MakeString(""),GLSL_Fragment=Data.Strings.MakeString(""),
 HLSL9_Vertex=Data.Strings.MakeString(""),HLSL9_Fragment=Data.Strings.MakeString("")};
 asset.HLSL11_VertexData.Data=File.ReadAllBytes(path(shader.GetProperty("vertex").GetString()));asset.HLSL11_VertexData.IsNull=false;
 asset.HLSL11_PixelData.Data=File.ReadAllBytes(path(shader.GetProperty("pixel").GetString()));asset.HLSL11_PixelData.IsNull=false;
 foreach(var a in template.VertexShaderAttributes)asset.VertexShaderAttributes.Add(new UndertaleShader.VertexShaderAttribute{Name=a.Name});
 Data.Shaders.Add(asset);
 var ctx=new GlobalDecompileContext(Data);
 var group=new CodeImportGroup(Data){AutoCreateAssets=true};
 foreach(var script in bundle.GetProperty("scripts").EnumerateArray()) group.QueueReplace(script.GetProperty("name").GetString(),File.ReadAllText(path(script.GetProperty("file").GetString())));
 foreach(var ev in bundle.GetProperty("events").EnumerateArray()) {
  var name="gml_Object_"+ev.GetProperty("object").GetString()+"_"+ev.GetProperty("type").GetString()+"_"+ev.GetProperty("subtype").GetInt32();
  var text=File.ReadAllText(path(ev.GetProperty("file").GetString()));
  var operation=ev.GetProperty("operation").GetString();
  if(operation!="replace") {
   var existing=Data.Code.FirstOrDefault(c=>c.Name.Content==name);
   var old=existing==null?"":new Underanalyzer.Decompiler.DecompileContext(ctx,existing,Data.ToolInfo.DecompilerSettings).DecompileToString();
   text=operation=="append"?old+"\n"+text:text+"\n"+old;
  }
  group.QueueReplace(name,text);
 }
 group.Import();
 foreach(var objName in bundle.GetProperty("persistent_objects").EnumerateArray()) {
  var obj=Data.GameObjects.First(o=>o.Name.Content==objName.GetString());obj.Persistent=true;obj.Visible=true;
 }
 Console.WriteLine("Applied composable patch: "+bundle.GetProperty("name").GetString());
}
