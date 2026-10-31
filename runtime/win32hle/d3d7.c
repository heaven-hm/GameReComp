/* A small Direct3D 7 fixed-function bridge over the screen's SDL OpenGL
 * context. The guest ABI remains 32-bit; guest structures and vertex buffers
 * are read through hle_ptr and never cast from guest addresses directly. */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <SDL2/SDL.h>
#include "win32hle.h"

#define D3D_OK 0u
#define E_NOINTERFACE 0x80004002u
#define E_POINTER 0x80004003u
#define D3DERR_INVALIDCALL 0x8876086Cu
#define D3DERR_NOTAVAILABLE 0x8876086Au

enum { GL_POINTS=0x0000, GL_LINES=0x0001, GL_LINE_STRIP=0x0003,
       GL_TRIANGLES=0x0004, GL_TRIANGLE_STRIP=0x0005,
       GL_TRIANGLE_FAN=0x0006, GL_COLOR_BUFFER_BIT=0x4000,
       GL_DEPTH_BUFFER_BIT=0x0100, GL_STENCIL_BUFFER_BIT=0x0400,
       GL_MODELVIEW=0x1700, GL_PROJECTION=0x1701, GL_TEXTURE=0x1702,
       GL_DEPTH_TEST=0x0B71, GL_BLEND=0x0BE2, GL_CULL_FACE=0x0B44,
       GL_DITHER=0x0BD0,
       GL_ALPHA_TEST=0x0BC0, GL_FOG=0x0B60, GL_FOG_MODE=0x0B65,
       GL_EXP=0x0800, GL_EXP2=0x0801, GL_SCISSOR_TEST=0x0C11,
       GL_SCISSOR_BOX=0x0C10, GL_VIEWPORT=0x0BA2, GL_LESS=0x0201,
       GL_COLOR_WRITEMASK=0x0C23, GL_DEPTH_WRITEMASK=0x0B72,
       GL_STENCIL_WRITEMASK=0x0B98, GL_COLOR_CLEAR_VALUE=0x0C22,
       GL_DEPTH_CLEAR_VALUE=0x0B73, GL_STENCIL_CLEAR_VALUE=0x0B91,
       GL_PACK_ALIGNMENT=0x0D05, GL_PACK_ROW_LENGTH=0x0D02,
       GL_FRAMEBUFFER=0x8D40, GL_RENDERBUFFER=0x8D41,
       GL_COLOR_ATTACHMENT0=0x8CE0, GL_DEPTH_ATTACHMENT=0x8D00,
       GL_FRAMEBUFFER_COMPLETE=0x8CD5, GL_DEPTH_COMPONENT24=0x81A6,
       GL_LEQUAL=0x0203, GL_GREATER=0x0204, GL_GEQUAL=0x0206,
       GL_EQUAL=0x0202, GL_NOTEQUAL=0x0205, GL_ALWAYS=0x0207,
       GL_NEVER=0x0200, GL_ZERO=0, GL_ONE=1, GL_SRC_ALPHA=0x0302,
       GL_ONE_MINUS_SRC_ALPHA=0x0303, GL_DST_ALPHA=0x0304,
       GL_ONE_MINUS_DST_ALPHA=0x0305, GL_DST_COLOR=0x0306,
       GL_ONE_MINUS_DST_COLOR=0x0307, GL_SRC_COLOR=0x0300,
       GL_ONE_MINUS_SRC_COLOR=0x0301, GL_SRC_ALPHA_SATURATE=0x0308,
       GL_FRONT_AND_BACK=0x0408,
       GL_POINT=0x1B00, GL_LINE=0x1B01, GL_FILL=0x1B02,
       GL_FLAT=0x1D00, GL_SMOOTH=0x1D01,
       GL_POLYGON_OFFSET_FILL=0x8037, GL_POLYGON_SMOOTH=0x0B41,
       GL_STENCIL_TEST=0x0B90, GL_KEEP=0x1E00, GL_INCR=0x1E02,
       GL_DECR=0x1E03, GL_INVERT=0x150A, GL_INCR_WRAP=0x8507,
       GL_DECR_WRAP=0x8508 };
enum { GL_LIGHTING=0x0B50, GL_COLOR_MATERIAL=0x0B57, GL_LIGHT0=0x4000,
       GL_MAX_LIGHTS=0x0D31, GL_AMBIENT=0x1200, GL_DIFFUSE=0x1201,
       GL_SPECULAR=0x1202, GL_POSITION=0x1203, GL_SPOT_DIRECTION=0x1204,
       GL_SPOT_EXPONENT=0x1205, GL_SPOT_CUTOFF=0x1206,
       GL_CONSTANT_ATTENUATION=0x1207, GL_LINEAR_ATTENUATION=0x1208,
       GL_QUADRATIC_ATTENUATION=0x1209, GL_EMISSION=0x1600,
       GL_SHININESS=0x1601, GL_FRONT=0x0404, GL_LIGHT_MODEL_AMBIENT=0x0B53,
       GL_LIGHT_MODEL_LOCAL_VIEWER=0x0B51, GL_NORMALIZE=0x0BA1,
       GL_DEPTH_BITS=0x0D56, GL_STENCIL_BITS=0x0D57,
       GL_MAX_CLIP_PLANES=0x0D32, GL_CLIP_PLANE0=0x3000,
       GL_MAX_TEXTURE_SIZE=0x0D33, GL_RENDERER=0x1F01,
       GL_AMBIENT_AND_DIFFUSE=0x1602, GL_COLOR_SUM=0x8458 };
enum { GL_TEXTURE_2D=0x0DE1, GL_TEXTURE_ENV=0x2300, GL_TEXTURE_ENV_MODE=0x2200,
       GL_MODULATE=0x2100, GL_REPLACE=0x1E01, GL_RGB=0x1907, GL_RGBA=0x1908,
       GL_COMBINE=0x8570, GL_COMBINE_RGB=0x8571, GL_COMBINE_ALPHA=0x8572,
       GL_SOURCE0_RGB=0x8580, GL_SOURCE1_RGB=0x8581,
       GL_SOURCE0_ALPHA=0x8588, GL_SOURCE1_ALPHA=0x8589,
       GL_OPERAND0_RGB=0x8590, GL_OPERAND1_RGB=0x8591,
       GL_OPERAND0_ALPHA=0x8598, GL_OPERAND1_ALPHA=0x8599,
       GL_CONSTANT=0x8576, GL_PRIMARY_COLOR=0x8577, GL_PREVIOUS=0x8578,
       GL_TEXTURE_ENV_COLOR=0x2201,
       GL_BGR=0x80E0, GL_BGRA=0x80E1, GL_UNSIGNED_BYTE=0x1401,
       GL_UNSIGNED_SHORT_5_6_5=0x8363, GL_TEXTURE_MIN_FILTER=0x2801,
       GL_TEXTURE_MAG_FILTER=0x2800, GL_TEXTURE_WRAP_S=0x2802,
       GL_TEXTURE_WRAP_T=0x2803, GL_NEAREST=0x2600, GL_LINEAR=0x2601,
       GL_REPEAT=0x2901, GL_MIRRORED_REPEAT=0x8370, GL_CLAMP_TO_EDGE=0x812F,
       GL_UNPACK_ALIGNMENT=0x0CF5, GL_UNPACK_ROW_LENGTH=0x0CF2 };
enum { D3DTA_COMPLEMENT=0x10, D3DTA_ALPHAREPLICATE=0x20,
       D3DTA_MODIFIER_MASK=0x30, D3DTA_ARGUMENT_MASK=0x0F };
/* Material colour sources (D3DMCS_*). Only COLOR1 has an equivalent here. */
enum { D3DMCS_MATERIAL=0, D3DMCS_COLOR1=1, D3DMCS_COLOR2=2 };

typedef void (*gl_begin_fn)(unsigned); typedef void (*gl_end_fn)(void);
typedef void (*gl_vertex3_fn)(float,float,float); typedef void (*gl_vertex4_fn)(float,float,float,float);
typedef void (*gl_color4ub_fn)(unsigned char,unsigned char,unsigned char,unsigned char);
typedef void (*gl_secondary_color3ub_fn)(unsigned char,unsigned char,unsigned char);
typedef void (*gl_normal3_fn)(float,float,float); typedef void (*gl_tex2_fn)(float,float);
typedef void (*gl_tex1_fn)(float);
typedef void (*gl_tex3_fn)(float,float,float); typedef void (*gl_tex4_fn)(float,float,float,float);
typedef void (*gl_matrix_mode_fn)(unsigned); typedef void (*gl_load_matrix_fn)(const float *);
typedef void (*gl_mult_matrix_fn)(const float *);
typedef void (*gl_enable_fn)(unsigned); typedef void (*gl_disable_fn)(unsigned);
typedef void (*gl_clear_color_fn)(float,float,float,float); typedef void (*gl_clear_depth_fn)(double);
typedef void (*gl_clear_fn)(unsigned); typedef void (*gl_depth_func_fn)(unsigned);
typedef void (*gl_depth_mask_fn)(unsigned char); typedef void (*gl_blend_fn)(unsigned,unsigned);
typedef void (*gl_depth_range_fn)(double,double);
typedef void (*gl_clear_stencil_fn)(int); typedef void (*gl_color_mask_fn)(unsigned char,unsigned char,unsigned char,unsigned char);
typedef void (*gl_stencil_mask_fn)(unsigned);
typedef void (*gl_stencil_func_fn)(unsigned,int,unsigned);
typedef void (*gl_stencil_op_fn)(unsigned,unsigned,unsigned);
typedef void (*gl_polygon_offset_fn)(float,float);
typedef void (*gl_alpha_fn)(unsigned,float); typedef void (*gl_cull_fn)(unsigned);
typedef void (*gl_front_fn)(unsigned); typedef void (*gl_polygon_fn)(unsigned,unsigned);
typedef void (*gl_shade_fn)(unsigned); typedef void (*gl_fogfv_fn)(unsigned,const float *);
typedef void (*gl_fogi_fn)(unsigned,int); typedef void (*gl_fogf_fn)(unsigned,float);
typedef void (*gl_getint_fn)(unsigned,int *);
typedef void (*gl_getfloat_fn)(unsigned,float *);
typedef const unsigned char *(*gl_getstring_fn)(unsigned);
typedef void (*gl_bind_texture_fn)(unsigned,unsigned);
typedef void (*gl_gen_textures_fn)(int,unsigned *);
typedef void (*gl_tex_image_2d_fn)(unsigned,int,int,int,int,int,unsigned,unsigned,const void *);
typedef void (*gl_tex_parameter_i_fn)(unsigned,unsigned,int);
typedef void (*gl_pixel_store_i_fn)(unsigned,int);
typedef void (*gl_tex_envi_fn)(unsigned,unsigned,int);
typedef void (*gl_tex_envfv_fn)(unsigned,unsigned,const float *);
typedef void (*gl_delete_textures_fn)(int,const unsigned *);
typedef void (*gl_materialfv_fn)(unsigned,unsigned,const float *);
typedef void (*gl_materialf_fn)(unsigned,unsigned,float);
typedef void (*gl_lightfv_fn)(unsigned,unsigned,const float *);
typedef void (*gl_lightf_fn)(unsigned,unsigned,float);
typedef void (*gl_color_material_fn)(unsigned,unsigned);
typedef void (*gl_light_modelfv_fn)(unsigned,const float *);
typedef void (*gl_light_modeli_fn)(unsigned,int);
typedef unsigned char (*gl_is_enabled_fn)(unsigned);
typedef void (*gl_scissor_fn)(int,int,int,int);
typedef void (*gl_clip_plane_fn)(unsigned,const double *);
typedef void (*gl_gen_objects_fn)(int,unsigned *);
typedef void (*gl_bind_object_fn)(unsigned,unsigned);
typedef void (*gl_delete_objects_fn)(int,const unsigned *);
typedef void (*gl_framebuffer_texture_fn)(unsigned,unsigned,unsigned,unsigned,int);
typedef unsigned (*gl_check_framebuffer_fn)(unsigned);
typedef void (*gl_renderbuffer_storage_fn)(unsigned,unsigned,int,int);
typedef void (*gl_framebuffer_renderbuffer_fn)(unsigned,unsigned,unsigned,unsigned);
typedef void (*gl_read_pixels_fn)(int,int,int,int,unsigned,unsigned,void *);
typedef void (*gl_viewport_fn)(int,int,int,int);

static gl_begin_fn p_begin; static gl_end_fn p_end;
static gl_vertex3_fn p_vertex3; static gl_vertex4_fn p_vertex4;
static gl_color4ub_fn p_color4ub; static gl_normal3_fn p_normal3; static gl_tex2_fn p_tex2;
static gl_secondary_color3ub_fn p_secondary_color3ub;
static gl_tex1_fn p_tex1;
static gl_tex3_fn p_tex3; static gl_tex4_fn p_tex4;
static gl_matrix_mode_fn p_matrix_mode; static gl_load_matrix_fn p_load_matrix;
static gl_mult_matrix_fn p_mult_matrix;
static gl_enable_fn p_enable; static gl_disable_fn p_disable;
static gl_clear_color_fn p_clear_color; static gl_clear_depth_fn p_clear_depth;
static gl_clear_fn p_clear; static gl_depth_func_fn p_depth_func;
static gl_depth_mask_fn p_depth_mask; static gl_blend_fn p_blend;
static gl_depth_range_fn p_depth_range;
static gl_clear_stencil_fn p_clear_stencil; static gl_color_mask_fn p_color_mask;
static gl_stencil_mask_fn p_stencil_mask;
static gl_stencil_func_fn p_stencil_func; static gl_stencil_op_fn p_stencil_op;
static gl_alpha_fn p_alpha; static gl_cull_fn p_cull; static gl_front_fn p_front;
static gl_polygon_fn p_polygon; static gl_shade_fn p_shade;
static gl_polygon_offset_fn p_polygon_offset;
static gl_fogfv_fn p_fogfv; static gl_fogi_fn p_fogi; static gl_fogf_fn p_fogf;
static gl_getint_fn p_getint;
static gl_getfloat_fn p_getfloat;
static gl_getstring_fn p_getstring;
static gl_bind_texture_fn p_bind_texture;
static gl_gen_textures_fn p_gen_textures;
static gl_tex_image_2d_fn p_tex_image_2d;
static gl_tex_parameter_i_fn p_tex_parameter_i;
static gl_pixel_store_i_fn p_pixel_store_i;
static gl_tex_envi_fn p_tex_envi;
static gl_tex_envfv_fn p_tex_envfv;
static gl_delete_textures_fn p_delete_textures;
static gl_materialfv_fn p_materialfv; static gl_materialf_fn p_materialf;
static gl_lightfv_fn p_lightfv; static gl_lightf_fn p_lightf;
static gl_color_material_fn p_color_material;
static gl_light_modelfv_fn p_light_modelfv;
static gl_light_modeli_fn p_light_modeli;
static gl_is_enabled_fn p_is_enabled;
static gl_scissor_fn p_scissor;
static gl_clip_plane_fn p_clip_plane;
static gl_gen_objects_fn p_gen_framebuffers,p_gen_renderbuffers;
static gl_bind_object_fn p_bind_framebuffer,p_bind_renderbuffer;
static gl_delete_objects_fn p_delete_framebuffers,p_delete_renderbuffers;
static gl_framebuffer_texture_fn p_framebuffer_texture;
static gl_check_framebuffer_fn p_check_framebuffer;
static gl_renderbuffer_storage_fn p_renderbuffer_storage;
static gl_framebuffer_renderbuffer_fn p_framebuffer_renderbuffer;
static gl_read_pixels_fn p_read_pixels;
static gl_viewport_fn p_gl_viewport;
static int g_gl_loaded;
static int g_fbo_loaded,g_fbo_available;
static unsigned g_rt_framebuffer,g_rt_color,g_rt_depth;
static int g_rt_width,g_rt_height;

#define LOAD_GL(field, type, symbol) do { *(void **)(&(field)) = SDL_GL_GetProcAddress(symbol); if (!(field)) return 0; } while (0)
static int load_gl(void) {
    if (g_gl_loaded) return 1;
    if (!hle_screen_gl_active()) return 0;
    LOAD_GL(p_begin, gl_begin_fn, "glBegin"); LOAD_GL(p_end, gl_end_fn, "glEnd");
    LOAD_GL(p_vertex3, gl_vertex3_fn, "glVertex3f"); LOAD_GL(p_vertex4, gl_vertex4_fn, "glVertex4f");
    LOAD_GL(p_color4ub, gl_color4ub_fn, "glColor4ub"); LOAD_GL(p_normal3, gl_normal3_fn, "glNormal3f");
    LOAD_GL(p_tex2, gl_tex2_fn, "glTexCoord2f"); LOAD_GL(p_tex1, gl_tex1_fn, "glTexCoord1f");
    LOAD_GL(p_tex3, gl_tex3_fn, "glTexCoord3f"); LOAD_GL(p_tex4, gl_tex4_fn, "glTexCoord4f");
    LOAD_GL(p_matrix_mode, gl_matrix_mode_fn, "glMatrixMode");
    LOAD_GL(p_load_matrix, gl_load_matrix_fn, "glLoadMatrixf"); LOAD_GL(p_mult_matrix, gl_mult_matrix_fn, "glMultMatrixf");
    LOAD_GL(p_enable, gl_enable_fn, "glEnable");
    LOAD_GL(p_disable, gl_disable_fn, "glDisable"); LOAD_GL(p_clear_color, gl_clear_color_fn, "glClearColor");
    LOAD_GL(p_clear_depth, gl_clear_depth_fn, "glClearDepth"); LOAD_GL(p_clear, gl_clear_fn, "glClear");
    LOAD_GL(p_depth_func, gl_depth_func_fn, "glDepthFunc"); LOAD_GL(p_depth_mask, gl_depth_mask_fn, "glDepthMask");
    LOAD_GL(p_depth_range, gl_depth_range_fn, "glDepthRange");
    LOAD_GL(p_clear_stencil, gl_clear_stencil_fn, "glClearStencil");
    LOAD_GL(p_color_mask, gl_color_mask_fn, "glColorMask"); LOAD_GL(p_stencil_mask, gl_stencil_mask_fn, "glStencilMask");
    LOAD_GL(p_stencil_func, gl_stencil_func_fn, "glStencilFunc"); LOAD_GL(p_stencil_op, gl_stencil_op_fn, "glStencilOp");
    LOAD_GL(p_polygon_offset, gl_polygon_offset_fn, "glPolygonOffset");
    LOAD_GL(p_blend, gl_blend_fn, "glBlendFunc"); LOAD_GL(p_alpha, gl_alpha_fn, "glAlphaFunc");
    LOAD_GL(p_cull, gl_cull_fn, "glCullFace"); LOAD_GL(p_front, gl_front_fn, "glFrontFace");
    LOAD_GL(p_polygon, gl_polygon_fn, "glPolygonMode"); LOAD_GL(p_shade, gl_shade_fn, "glShadeModel");
    LOAD_GL(p_fogfv, gl_fogfv_fn, "glFogfv"); LOAD_GL(p_fogi, gl_fogi_fn, "glFogi");
    LOAD_GL(p_fogf, gl_fogf_fn, "glFogf"); LOAD_GL(p_getint, gl_getint_fn, "glGetIntegerv");
    LOAD_GL(p_getfloat, gl_getfloat_fn, "glGetFloatv");
    LOAD_GL(p_getstring, gl_getstring_fn, "glGetString");
    LOAD_GL(p_bind_texture, gl_bind_texture_fn, "glBindTexture");
    LOAD_GL(p_gen_textures, gl_gen_textures_fn, "glGenTextures");
    LOAD_GL(p_tex_image_2d, gl_tex_image_2d_fn, "glTexImage2D");
    LOAD_GL(p_tex_parameter_i, gl_tex_parameter_i_fn, "glTexParameteri");
    LOAD_GL(p_pixel_store_i, gl_pixel_store_i_fn, "glPixelStorei");
    LOAD_GL(p_tex_envi, gl_tex_envi_fn, "glTexEnvi");
    LOAD_GL(p_tex_envfv, gl_tex_envfv_fn, "glTexEnvfv");
    LOAD_GL(p_delete_textures, gl_delete_textures_fn, "glDeleteTextures");
    LOAD_GL(p_materialfv, gl_materialfv_fn, "glMaterialfv");
    LOAD_GL(p_materialf, gl_materialf_fn, "glMaterialf");
    LOAD_GL(p_lightfv, gl_lightfv_fn, "glLightfv");
    LOAD_GL(p_lightf, gl_lightf_fn, "glLightf");
    LOAD_GL(p_color_material, gl_color_material_fn, "glColorMaterial");
    LOAD_GL(p_light_modelfv, gl_light_modelfv_fn, "glLightModelfv");
    LOAD_GL(p_light_modeli, gl_light_modeli_fn, "glLightModeli");
    LOAD_GL(p_is_enabled, gl_is_enabled_fn, "glIsEnabled");
    LOAD_GL(p_scissor, gl_scissor_fn, "glScissor");
    LOAD_GL(p_clip_plane, gl_clip_plane_fn, "glClipPlane");
    LOAD_GL(p_gl_viewport, gl_viewport_fn, "glViewport");
    *(void **)(&p_secondary_color3ub)=SDL_GL_GetProcAddress("glSecondaryColor3ub");
    g_gl_loaded = 1;
    return 1;
}
#undef LOAD_GL

static void *gl_proc_pair(const char *core,const char *extension) {
    void *p=SDL_GL_GetProcAddress(core);
    return p?p:SDL_GL_GetProcAddress(extension);
}
static int load_fbo(void) {
    if(g_fbo_loaded) return g_fbo_available;
#define LOAD_FBO(field,type,core,extension) do { *(void **)(&(field))=gl_proc_pair(core,extension); if(!(field))goto unavailable; } while(0)
    LOAD_FBO(p_gen_framebuffers,gl_gen_objects_fn,"glGenFramebuffers","glGenFramebuffersEXT");
    LOAD_FBO(p_bind_framebuffer,gl_bind_object_fn,"glBindFramebuffer","glBindFramebufferEXT");
    LOAD_FBO(p_delete_framebuffers,gl_delete_objects_fn,"glDeleteFramebuffers","glDeleteFramebuffersEXT");
    LOAD_FBO(p_framebuffer_texture,gl_framebuffer_texture_fn,"glFramebufferTexture2D","glFramebufferTexture2DEXT");
    LOAD_FBO(p_check_framebuffer,gl_check_framebuffer_fn,"glCheckFramebufferStatus","glCheckFramebufferStatusEXT");
    LOAD_FBO(p_gen_renderbuffers,gl_gen_objects_fn,"glGenRenderbuffers","glGenRenderbuffersEXT");
    LOAD_FBO(p_bind_renderbuffer,gl_bind_object_fn,"glBindRenderbuffer","glBindRenderbufferEXT");
    LOAD_FBO(p_delete_renderbuffers,gl_delete_objects_fn,"glDeleteRenderbuffers","glDeleteRenderbuffersEXT");
    LOAD_FBO(p_renderbuffer_storage,gl_renderbuffer_storage_fn,"glRenderbufferStorage","glRenderbufferStorageEXT");
    LOAD_FBO(p_framebuffer_renderbuffer,gl_framebuffer_renderbuffer_fn,"glFramebufferRenderbuffer","glFramebufferRenderbufferEXT");
    LOAD_FBO(p_read_pixels,gl_read_pixels_fn,"glReadPixels","glReadPixels");
    g_fbo_available=1; g_fbo_loaded=1;
#undef LOAD_FBO
    return 1;
unavailable:
    g_fbo_available=0; g_fbo_loaded=1;
#undef LOAD_FBO
    return 0;
}

static int target_info(uint32_t surface, uint8_t **pixels, int *w, int *h, int *pitch, int *bpp) {
    return hle_dd_surface_is_3d_target(surface) &&
           hle_dd_surface_pixels(surface,pixels,w,h,pitch,bpp) &&
           *w>0 && *h>0 && (*bpp==16 || *bpp==24 || *bpp==32) &&
           (int64_t)*pitch>=(int64_t)*w*(*bpp/8);
}

static unsigned mask_shift(uint32_t m) { unsigned s=0; if(!m) return 0; while(!(m&1u)) { m>>=1; s++; } return s; }
static uint32_t mask_max(uint32_t m) { if(!m) return 1; while(!(m&1u)) m>>=1; return m; }
static uint32_t read_px(const uint8_t *p,int bpp) {
    if(bpp==16) return (uint32_t)p[0] | ((uint32_t)p[1]<<8);
    if(bpp==24) return (uint32_t)p[0] | ((uint32_t)p[1]<<8) | ((uint32_t)p[2]<<16);
    return (uint32_t)p[0] | ((uint32_t)p[1]<<8) | ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24);
}
/* Convert tightly packed rows of a DirectDraw surface format to RGBA8. */
static uint8_t *rgba8_from_pixels(const uint8_t *px,int w,int h,int pitch,int bpp,
                                  uint32_t rm,uint32_t gm,uint32_t bm,uint32_t am) {
    if(w<=0||h<=0||(bpp!=16&&bpp!=24&&bpp!=32)||pitch<=0) return NULL;
    size_t row=(size_t)w*4u;
    if((size_t)h>SIZE_MAX/row) return NULL;
    uint8_t *out=(uint8_t *)malloc(row*(size_t)h);
    if(!out) return NULL;
    unsigned rs=mask_shift(rm),gs=mask_shift(gm),bs=mask_shift(bm),as=mask_shift(am);
    uint32_t rmax=mask_max(rm),gmax=mask_max(gm),bmax=mask_max(bm),amax=mask_max(am);
    int step=bpp/8;
    for(int y=0;y<h;y++) {
        const uint8_t *src=px+(size_t)y*(size_t)pitch;
        uint8_t *dst=out+(size_t)y*row;
        for(int x=0;x<w;x++,src+=step,dst+=4) {
            uint32_t v=read_px(src,bpp);
            dst[0]=(uint8_t)((((v&rm)>>rs)*255u)/rmax);
            dst[1]=(uint8_t)((((v&gm)>>gs)*255u)/gmax);
            dst[2]=(uint8_t)((((v&bm)>>bs)*255u)/bmax);
            dst[3]=am?(uint8_t)((((v&am)>>as)*255u)/amax):255u;
        }
    }
    return out;
}
/* The GL pixel path for a surface format; 0 when it needs converting. */
static int gl_pixel_format(int bpp,uint32_t rm,uint32_t gm,uint32_t bm,uint32_t am,
                           unsigned *format,unsigned *type,unsigned *internal) {
    if(bpp==16 && rm==0xF800 && gm==0x07E0 && bm==0x001F && !am) {
        *format=GL_RGB, *type=GL_UNSIGNED_SHORT_5_6_5, *internal=GL_RGB; return 1;
    }
    if(bpp==24 && rm==0xFF0000 && gm==0x00FF00 && bm==0x0000FF) {
        *format=GL_BGR, *type=GL_UNSIGNED_BYTE, *internal=GL_RGB; return 1;
    }
    if(bpp==32 && rm==0x00FF0000 && gm==0x0000FF00 && bm==0x000000FF) {
        *format=GL_BGRA, *type=GL_UNSIGNED_BYTE, *internal=am?GL_RGBA:GL_RGB; return 1;
    }
    return 0;
}

/* The SDL window's default framebuffer presents the primary. A single
 * reusable FBO backs other DirectDraw 3D surfaces; EndScene copies it back
 * into the surface's CPU pixels before that surface can be blitted/flipped. */
static int bind_render_target(uint32_t surface) {
    uint8_t *pixels; int w,h,pitch,bpp;
    if(!load_gl() || !target_info(surface,&pixels,&w,&h,&pitch,&bpp)) return 0;
    if(hle_dd_surface_is_primary_3d_target(surface)) {
        if(load_fbo()) p_bind_framebuffer(GL_FRAMEBUFFER,0);
        return 1;
    }
    if(!load_fbo()) return 0;
    if(!g_rt_framebuffer) {
        p_gen_framebuffers(1,&g_rt_framebuffer);
        p_gen_textures(1,&g_rt_color);
        p_gen_renderbuffers(1,&g_rt_depth);
        if(!g_rt_framebuffer || !g_rt_color || !g_rt_depth) {
            if(g_rt_framebuffer) p_delete_framebuffers(1,&g_rt_framebuffer);
            if(g_rt_color) p_delete_textures(1,&g_rt_color);
            if(g_rt_depth) p_delete_renderbuffers(1,&g_rt_depth);
            g_rt_framebuffer=g_rt_color=g_rt_depth=0;
            return 0;
        }
    }
    uint32_t rm,gm,bm,am;
    if(!hle_dd_surface_format(surface,&bpp,&rm,&gm,&bm,&am)) return 0;
    int bytes_per_pixel=bpp/8;
    size_t row_bytes=(size_t)w*(size_t)bytes_per_pixel;
    if(pitch<=0 || (size_t)pitch<row_bytes) return 0;
    uint8_t *packed=NULL;
    const uint8_t *source=pixels;
    if((size_t)pitch!=row_bytes) {
        if((size_t)h>SIZE_MAX/row_bytes) return 0;
        packed=(uint8_t *)malloc(row_bytes*(size_t)h);
        if(!packed) return 0;
        for(int y=0;y<h;y++) memcpy(packed+(size_t)y*row_bytes,pixels+(size_t)y*(size_t)pitch,row_bytes);
        source=packed;
    }
    unsigned format,type,internal;
    uint8_t *converted=NULL;
    if(!gl_pixel_format(bpp,rm,gm,bm,am,&format,&type,&internal)) {
        converted=rgba8_from_pixels(source,w,h,(int)row_bytes,bpp,rm,gm,bm,am);
        if(!converted) { free(packed); return 0; }
        format=GL_RGBA; type=GL_UNSIGNED_BYTE; internal=GL_RGBA;
    }
    p_bind_framebuffer(GL_FRAMEBUFFER,g_rt_framebuffer);
    p_bind_texture(GL_TEXTURE_2D,g_rt_color);
    p_pixel_store_i(GL_UNPACK_ALIGNMENT,1);
    p_pixel_store_i(GL_UNPACK_ROW_LENGTH,0);
    p_tex_image_2d(GL_TEXTURE_2D,0,internal,w,h,0,format,type,converted?converted:source);
    free(converted);
    free(packed);
    p_pixel_store_i(GL_UNPACK_ROW_LENGTH,0);
    p_pixel_store_i(GL_UNPACK_ALIGNMENT,4);
    p_tex_parameter_i(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_NEAREST);
    p_tex_parameter_i(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_NEAREST);
    p_framebuffer_texture(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,g_rt_color,0);
    p_bind_renderbuffer(GL_RENDERBUFFER,g_rt_depth);
    if(g_rt_width!=w || g_rt_height!=h) {
        p_renderbuffer_storage(GL_RENDERBUFFER,GL_DEPTH_COMPONENT24,w,h);
        g_rt_width=w; g_rt_height=h;
    }
    p_framebuffer_renderbuffer(GL_FRAMEBUFFER,GL_DEPTH_ATTACHMENT,GL_RENDERBUFFER,g_rt_depth);
    if(p_check_framebuffer(GL_FRAMEBUFFER)!=GL_FRAMEBUFFER_COMPLETE) {
        p_bind_framebuffer(GL_FRAMEBUFFER,0);
        return 0;
    }
    p_gl_viewport(0,0,w,h);
    return 1;
}

static int store_render_target(uint32_t surface) {
    uint8_t *pixels; int w,h,pitch,bpp;
    if(hle_dd_surface_is_primary_3d_target(surface)) return 1;
    if(!target_info(surface,&pixels,&w,&h,&pitch,&bpp) || !load_fbo()) return 0;
    size_t row_bytes=(size_t)w*4u;
    if((size_t)h>SIZE_MAX/row_bytes) return 0;
    uint8_t *rgba=(uint8_t *)malloc(row_bytes*(size_t)h);
    if(!rgba) return 0;
    p_bind_framebuffer(GL_FRAMEBUFFER,g_rt_framebuffer);
    int old_pack=4,old_row_length=0; p_getint(GL_PACK_ALIGNMENT,&old_pack); p_getint(GL_PACK_ROW_LENGTH,&old_row_length);
    p_pixel_store_i(GL_PACK_ALIGNMENT,1); p_pixel_store_i(GL_PACK_ROW_LENGTH,0);
    p_read_pixels(0,0,w,h,GL_RGBA,GL_UNSIGNED_BYTE,rgba);
    p_pixel_store_i(GL_PACK_ALIGNMENT,old_pack); p_pixel_store_i(GL_PACK_ROW_LENGTH,old_row_length);
    uint32_t rm=0,gm=0,bm=0,am=0;
    if(!hle_dd_surface_format(surface,&bpp,&rm,&gm,&bm,&am)) { free(rgba); return 0; }
    unsigned rs=mask_shift(rm),gs=mask_shift(gm),bs=mask_shift(bm),as=mask_shift(am);
    uint32_t rmax=mask_max(rm),gmax=mask_max(gm),bmax=mask_max(bm),amax=mask_max(am);
    for(int y=0;y<h;y++) {
        const uint8_t *src=rgba+(size_t)(h-1-y)*row_bytes;
        uint8_t *dst=pixels+(size_t)y*(size_t)pitch;
        for(int x=0;x<w;x++) {
            uint32_t r=src[x*4], g=src[x*4+1], b=src[x*4+2], a=src[x*4+3];
            uint32_t v=((((r*rmax+127u)/255u)<<rs)&rm) | ((((g*gmax+127u)/255u)<<gs)&gm) |
                       ((((b*bmax+127u)/255u)<<bs)&bm);
            if(am) v|=(uint32_t)((((a*amax+127u)/255u)<<as)&am);
            if(bpp==16) { uint16_t pv=(uint16_t)v; memcpy(dst+(size_t)x*2,&pv,2); }
            else if(bpp==24) { dst[x*3]=(uint8_t)v; dst[x*3+1]=(uint8_t)(v>>8); dst[x*3+2]=(uint8_t)(v>>16); }
            else { memcpy(dst+(size_t)x*4,&v,4); }
        }
    }
    free(rgba);
    return 1;
}

static void set_target_viewport(uint32_t surface,int x,int y,int w,int h) {
    if(hle_dd_surface_is_primary_3d_target(surface)) hle_screen_gl_viewport(x,y,w,h);
    else p_gl_viewport(x,(int)g_rt_height-y-h,w,h);
}

static const uint8_t IID_IUNKNOWN[16] = {0,0,0,0,0,0,0,0,0xC0,0,0,0,0,0,0,0x46};
/* IID_IDirect3D7 is {f5049e77-4861-11d2-a407-00a0c90629a8}. The 0x78 form is
 * the one this layer used to accept; both are served so a title holding the
 * documented GUID resolves. */
static const uint8_t IID_IDIRECT3D7[16] = {0x77,0x9E,0x04,0xF5,0x61,0x48,0xD2,0x11,0xA4,0x07,0,0xA0,0xC9,0x06,0x29,0xA8};
static const uint8_t IID_IDIRECT3D7_ALT[16] = {0x78,0x9E,0x04,0xF5,0x61,0x48,0xD2,0x11,0xA4,0x07,0,0xA0,0xC9,0x06,0x29,0xA8};
static const uint8_t IID_IDIRECT3DDEVICE7[16] = {0x79,0x9E,0x04,0xF5,0x61,0x48,0xD2,0x11,0xA4,0x07,0,0xA0,0xC9,0x06,0x29,0xA8};
static const uint8_t IID_IDIRECT3DVERTEXBUFFER7[16] = {0x7D,0x9E,0x04,0xF5,0x61,0x48,0xD2,0x11,0xA4,0x07,0,0xA0,0xC9,0x06,0x29,0xA8};
static uint32_t g_d3d_vt, g_d3d;
static uint32_t g_dev_vt;
static uint32_t g_vb_vt;
static uint32_t g_device;
static uint32_t g_render_target;
static int g_scene_active;
static float g_world[16], g_view[16], g_projection[16];
static float g_texture_transform[16];
static uint32_t g_render[256];
static uint32_t g_viewport[6];
static uint32_t g_texture_surface;
static unsigned g_gl_texture;
static uint32_t g_stage_state[32];
static uint8_t g_material[68];
static uint8_t g_lights[8][104];
static uint8_t g_light_valid[8], g_light_enabled[8];
static unsigned g_max_lights;
static unsigned g_max_clip_planes;
static float g_clip_planes[6][4];
static uint8_t g_clip_plane_valid[6];

#define D3D7_DEVICE_DESC_SIZE 236u
#define D3D7_PRIM_CAPS_SIZE 56u
/* Direct3D 7 clients pass D3DDEVICEDESC7.deviceGUID back to CreateDevice, and
 * they identify a hardware device by that GUID, not by the capability bits: a
 * title looking for acceleration enumerates until it sees the HAL identity and
 * refuses a driver whose only device is the RGB software rasteriser. So the
 * backend advertises the same identities a Windows display driver does. Both
 * draw through the same OpenGL fixed-function path, which transforms and
 * lights on the GPU, hence the HAL description for either. */
static const uint8_t g_d3d7_hal_guid[16] = {      /* IID_IDirect3DHALDevice */
    0xE0,0x3D,0xE6,0x84, 0xAA,0x46, 0xCF,0x11,
    0x81,0x6F,0x00,0x00,0xC0,0x20,0x15,0x6E
};
static const uint8_t g_d3d7_tnlhal_guid[16] = {   /* IID_IDirect3DTnLHalDevice */
    0x78,0x9E,0x04,0xF5, 0x61,0x48, 0xD2,0x11,
    0xA4,0x07,0x00,0xA0,0xC9,0x06,0x29,0xA8
};
static const uint8_t g_d3d7_gl_device_guid[16] = { /* IID_IDirect3DRGBDevice */
    0x60,0x5C,0x66,0xA4, 0x73,0x26, 0xCF,0x11,
    0xA3,0x1A,0x00,0xAA,0x00,0xB9,0x33,0x56
};
static uint8_t g_d3d7_created_guid[16];
static int g_d3d7_created_tnl;

static int gl_rasterises_on_hardware(void);
static int d3d7_guid_is(const uint8_t *a, const uint8_t *b) { return memcmp(a,b,16)==0; }
/* CreateDevice accepts the RGB software identity even while it is not
 * enumerated (Windows always offers it); a HAL identity is only honoured when
 * this backend actually has a hardware GL renderer behind it. */
static int d3d7_device_available(const uint8_t *guid) {
    if (d3d7_guid_is(guid,g_d3d7_gl_device_guid)) return 1;
    return gl_rasterises_on_hardware() &&
           (d3d7_guid_is(guid,g_d3d7_hal_guid) || d3d7_guid_is(guid,g_d3d7_tnlhal_guid));
}

static void dev_get_direct3d(void);
static void dev_set_target(void);
static void dev_get_target(void);
static void dev_begin_scene(void);
static void dev_end_scene(void);
static void dev_clear(void);
static void dev_set_transform(void);
static void dev_get_transform(void);
static void dev_set_viewport(void);
static void dev_get_viewport(void);
static void dev_multiply_transform(void);
static void dev_set_render_state(void);
static void dev_get_render_state(void);
static void dev_draw_primitive(void);
static void dev_draw_indexed_primitive(void);
static int stage0_texture_active(void);
static int stage0_factor_active(void);
static void dev_draw_vb(void);
static void dev_draw_indexed_vb(void);
static void d3d_create_vb(void);
static void dev_get_texture(void);
static void dev_set_texture(void);
static void dev_get_tss(void);
static void dev_set_tss(void);
static void dev_validate(void);
static void refresh_lights(void);
static void refresh_clip_planes(void);
static void apply_material(void);
static void apply_color_material_state(void);
static void dev_set_material(void);
static void dev_get_material(void);
static void dev_set_light(void);
static void dev_get_light(void);
static void dev_light_enable(void);
static void dev_get_light_enable(void);
static void dev_set_clip_plane(void);
static void dev_get_clip_plane(void);
static void dev_set_clip(void);
static void dev_get_clip(void);
static void dev_get_caps(void);
static void dev_enum_textures(void);
static void d3d_enum_z(void);
static void d3d_evict(void);
static void dev_draw_strided(void);
static void dev_draw_indexed_strided(void);
static void vb_lock(void);
static void vb_unlock(void);
static void vb_get_desc(void);
static void release_surface_ref(uint32_t surface);

#define UNIMPL(name, argc) static void name(void) { RET(D3DERR_NOTAVAILABLE, argc); }
UNIMPL(dev_begin_state,1)
UNIMPL(dev_end_state,2) UNIMPL(dev_preload,2)
UNIMPL(dev_sphere,6)
UNIMPL(dev_apply_state,2) UNIMPL(dev_capture_state,2) UNIMPL(dev_delete_state,2)
UNIMPL(dev_create_state,3) UNIMPL(dev_load,6)
UNIMPL(dev_get_info,4) UNIMPL(vb_process_vertices,8) UNIMPL(vb_optimize,3)
UNIMPL(vb_process_strided,8)
#undef UNIMPL

static void identity(float *m) { memset(m,0,16*sizeof(float)); m[0]=m[5]=m[10]=m[15]=1.f; }
static void refresh_modelview(void) {
    if (!load_gl()) return;
    p_matrix_mode(GL_MODELVIEW); p_load_matrix(g_view); p_mult_matrix(g_world);
}
static void load_projection(void) {
    if (!load_gl()) return;
    float m[16]; memcpy(m,g_projection,sizeof m);
    /* D3D clip depth is [0,1], while OpenGL clip depth is [-1,1]. */
    for (int c=0;c<4;c++) m[c*4+2]=2.f*m[c*4+2]-m[c*4+3];
    p_matrix_mode(GL_PROJECTION); p_load_matrix(m);
}
static void apply_texture_transform(void) {
    if(!load_gl()) return;
    float matrix[16];
    uint32_t flags=g_stage_state[24];
    if(!(flags&7u)) identity(matrix); else memcpy(matrix,g_texture_transform,sizeof matrix);
    if(!(flags&0x100u)) matrix[3]=matrix[7]=matrix[11]=0.f,matrix[15]=1.f;
    p_matrix_mode(GL_TEXTURE); p_load_matrix(matrix); p_matrix_mode(GL_MODELVIEW);
}
static unsigned gl_cmp(uint32_t v) {
    static const unsigned t[]={GL_NEVER,GL_NEVER,GL_LESS,GL_EQUAL,GL_LEQUAL,GL_GREATER,GL_NOTEQUAL,GL_GEQUAL,GL_ALWAYS};
    return v<sizeof(t)/sizeof(t[0])?t[v]:GL_LEQUAL;
}
static unsigned gl_blend(uint32_t v) {
    static const unsigned t[]={GL_ZERO,GL_ZERO,GL_ONE,GL_SRC_COLOR,GL_ONE_MINUS_SRC_COLOR,GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA,GL_DST_ALPHA,GL_ONE_MINUS_DST_ALPHA,GL_DST_COLOR,GL_ONE_MINUS_DST_COLOR};
    if (v==11) return GL_SRC_ALPHA_SATURATE;
    return v<sizeof(t)/sizeof(t[0])?t[v]:GL_ONE;
}
static uint32_t vertex_stride(uint32_t fvf) {
    uint32_t p=fvf&0x00e, n=(p==4?4:3), s=n*4;
    if (p!=2 && p!=4) return 0;
    if (p==4 && (fvf&0x010)) return 0;
    if (fvf&0x010) s+=12;
    if (fvf&0x040) s+=4;
    if (fvf&0x080) s+=4;
    /* Up to eight texture-coordinate sets, each 1-4 floats (FVF half of the
     * texcoord declaration). Multi-textured titles (base + lightmap) use two;
     * rejecting those made CreateVertexBuffer fail and the game erased its
     * own buffer pointer on the ignored HRESULT. */
    uint32_t tc=(fvf>>8)&15;
    if (tc>8) return 0;
    for (uint32_t i=0;i<tc;i++) {
        uint32_t dimcode=(fvf>>(16+i*2))&3;
        s+=4*(dimcode==3?1:dimcode==1?3:dimcode==2?4:2);
    }
    return s;
}
static unsigned gl_primitive(uint32_t p) {
    switch(p) { case 1:return GL_POINTS; case 2:return GL_LINES; case 3:return GL_LINE_STRIP;
    case 4:return GL_TRIANGLES; case 5:return GL_TRIANGLE_STRIP; case 6:return GL_TRIANGLE_FAN; default:return 0xffffffffu; }
}
static void apply_stage0_state(void);

static uint32_t texcoord_offset(uint32_t fvf,uint32_t index) {
    uint32_t off=((fvf&0x00e)==4?16:12);
    if(fvf&0x010) off+=12;
    if(fvf&0x040) off+=4;
    if(fvf&0x080) off+=4;
    for(uint32_t i=0;i<index;i++) {
        uint32_t dimcode=(fvf>>(16+i*2))&3;
        off+=4*(dimcode==3?1:dimcode==1?3:dimcode==2?4:2);
    }
    return off;
}
static void emit_vertex(const uint8_t *v, uint32_t fvf, int screen_xy) {
    uint32_t pos=fvf&0x00e, off=(pos==4?16:12);
    float x,y,z;
    memcpy(&x,v,4); memcpy(&y,v+4,4); memcpy(&z,v+8,4);
    if(fvf&0x010) { float n[3]; memcpy(n,v+off,12); p_normal3(n[0],n[1],n[2]); off+=12; }
    if(fvf&0x040) { uint32_t c; memcpy(&c,v+off,4); p_color4ub((uint8_t)(c>>16),(uint8_t)(c>>8),(uint8_t)c,(uint8_t)(c>>24)); off+=4; }
    if(fvf&0x080) {
        uint32_t specular; memcpy(&specular,v+off,4);
        if(g_render[29]) p_secondary_color3ub((uint8_t)(specular>>16),(uint8_t)(specular>>8),(uint8_t)specular);
        else p_secondary_color3ub(0,0,0);
        off+=4;
    }
    uint32_t tc=(fvf>>8)&15;
    if(tc>0) {
        /* TEXCOORDINDEX (state 11) picks which declared set feeds stage 0;
         * lightmapped geometry declares two and renders base in stage 0. */
        uint32_t set=g_stage_state[11]&0xFFFFu;
        if(set>=tc) set=0;
        off=texcoord_offset(fvf,set);
        uint32_t dimcode=(fvf>>(16+set*2))&3;
        if(dimcode==3) { float u; memcpy(&u,v+off,4); p_tex1(u); }
        else if(dimcode==1) { float uvw[3]; memcpy(uvw,v+off,12); p_tex3(uvw[0],uvw[1],uvw[2]); }
        else if(dimcode==2) { float uvwq[4]; memcpy(uvwq,v+off,16); p_tex4(uvwq[0],uvwq[1],uvwq[2],uvwq[3]); }
        else { float uv[2]; memcpy(uv,v+off,8); p_tex2(uv[0],uv[1]); }
    }
    if(screen_xy) {
        float rhw; memcpy(&rhw,v+12,4);
        float inv_w=1.f/rhw;
        float w=(float)g_viewport[2],h=(float)g_viewport[3];
        float ndc_x=2.f*(x-(float)g_viewport[0])/w-1.f;
        float ndc_y=1.f-2.f*(y-(float)g_viewport[1])/h;
        float min_z,max_z; memcpy(&min_z,&g_viewport[4],4); memcpy(&max_z,&g_viewport[5],4);
        float range=max_z-min_z;
        float ndc_z=range!=0.f?2.f*(z-min_z)/range-1.f:0.f;
        p_vertex4(ndc_x*inv_w,ndc_y*inv_w,ndc_z*inv_w,inv_w);
    } else p_vertex3(x,y,z);
}
static unsigned char color_byte(float f) {
    int v=(int)(f*255.f+0.5f);
    return (unsigned char)(v<0?0:v>255?255:v);
}
static void use_material_diffuse(void) {
    /* Vertices without D3DFVF_DIFFUSE take the current material diffuse --
     * with lighting disabled that is the drawn colour. Without this, GL
     * kept the previous draw's glColor and textured geometry came out black
     * whenever a HUD pass had left the colour at zero. */
    p_color4ub(color_byte(g_material[0]),color_byte(g_material[1]),
               color_byte(g_material[2]),color_byte(g_material[3]));
}
static unsigned g_draw_calls,g_draw_ok,g_draw_reject[12];
static uint32_t g_last_fvf;
static uint32_t g_fvf_hist_key[8]; static unsigned g_fvf_hist_n[8];
static uint32_t g_tex_hist_tex[32], g_tex_hist_fvf[32]; static unsigned g_tex_hist_n[32];
static uint32_t g_mt_tex[16]; static unsigned g_mt_n[16];
static unsigned g_lit_draws,g_unlit_draws;
static int diag_notex(void) { static int v=-1; if(v<0) v=getenv("RECOMP_D3D7_NOTEX")!=NULL; return v; }
#define REJECT(n) do { g_draw_reject[n]++; return 0; } while(0)
static int draw_vertices(uint32_t primitive, uint32_t fvf, uint32_t vertices,
                         uint32_t vertex_count, uint32_t indices, uint32_t index_count) {
    unsigned mode=gl_primitive(primitive); uint32_t stride=vertex_stride(fvf);
    g_draw_calls++; g_last_fvf=fvf;
    for(unsigned i=0;i<8;i++) {
        if(g_fvf_hist_n[i] && g_fvf_hist_key[i]==fvf) { g_fvf_hist_n[i]++; break; }
        if(!g_fvf_hist_n[i]) { g_fvf_hist_key[i]=fvf; g_fvf_hist_n[i]=1; break; }
    }
    if(g_render[137]) g_lit_draws++; else g_unlit_draws++;
    for(unsigned i=0;i<32;i++) {
        if(g_tex_hist_n[i] && g_tex_hist_tex[i]==g_texture_surface && g_tex_hist_fvf[i]==fvf) { g_tex_hist_n[i]++; break; }
        if(!g_tex_hist_n[i]) { g_tex_hist_tex[i]=g_texture_surface; g_tex_hist_fvf[i]=fvf; g_tex_hist_n[i]=1; break; }
    }
    if((fvf&0xF00)==0x200) for(unsigned i=0;i<16;i++) {
        if(g_mt_n[i] && g_mt_tex[i]==g_texture_surface) { g_mt_n[i]++; break; }
        if(!g_mt_n[i]) { g_mt_tex[i]=g_texture_surface; g_mt_n[i]=1; break; }
    }
    if(!g_scene_active) REJECT(0);
    if(!load_gl() || mode==0xffffffffu || !stride || !vertices) REJECT(1);
    if(!vertex_count || (indices && !index_count) || (!indices && index_count) ||
       vertex_count>1000000u || index_count>3000000u) REJECT(2);
    const uint16_t *idx=indices?(const uint16_t *)hle_ptr(indices):NULL;
    const uint8_t *base=hle_ptr(vertices);
    if(idx) {
        for(uint32_t i=0;i<index_count;i++) if(idx[i]>=vertex_count) REJECT(3);
    }
    if((fvf&0x00e)==4) {
        for(uint32_t i=0;i<(idx?index_count:vertex_count);i++) {
            uint32_t vi=idx?idx[i]:i; float rhw;
            memcpy(&rhw,base+(size_t)vi*stride+12,4);
            if(!isfinite(rhw) || rhw<=0.f || !isfinite(1.f/rhw)) REJECT(4);
        }
    }
    int has_secondary=(fvf&0x080)!=0, color_sum_was_enabled=0;
    if(has_secondary && !p_secondary_color3ub) REJECT(5);
    if(g_texture_surface && stage0_texture_active() && !diag_notex() && ((fvf >> 8) & 15)) {
        uint8_t *pixels; int w,h,pitch,bpp;
        if(!hle_dd_surface_pixels(g_texture_surface,&pixels,&w,&h,&pitch,&bpp) || w<=0 || h<=0) REJECT(6);
        { static unsigned mtlog;
          if(getenv("RECOMP_D3D7_DIAG") && (fvf&0xF00)==0x200 && mtlog<24) {
              mtlog++; uint32_t f=read_px(pixels,bpp), m=read_px(pixels+(size_t)(h/2)*(size_t)pitch+(size_t)(w/2)*(size_t)(bpp/8),bpp);
              unsigned lk=0,nz=0; hle_dd_surface_stats(g_texture_surface,&lk,&nz);
              fprintf(stderr,"[d3d7] multi-draw %08x %dx%d bpp=%d first=%08x mid=%08x locks=%u nz=%u\n",g_texture_surface,w,h,bpp,f,m,lk,nz);
          } }
        uint32_t rm,gm,bm,am;
        if(!hle_dd_surface_format(g_texture_surface,&bpp,&rm,&gm,&bm,&am)) REJECT(7);
        int bytes_per_pixel=bpp/8;
        if(pitch<=0 || (size_t)w>SIZE_MAX/(size_t)bytes_per_pixel) REJECT(7);
        size_t row_bytes=(size_t)w*(size_t)bytes_per_pixel;
        if((size_t)pitch<row_bytes) REJECT(7);
        uint8_t *packed=NULL;
        const uint8_t *source=pixels;
        if((size_t)pitch!=row_bytes) {
            if((size_t)h>SIZE_MAX/row_bytes) REJECT(7);
            packed=(uint8_t *)malloc(row_bytes*(size_t)h);
            if(!packed) REJECT(7);
            for(int y=0;y<h;y++)
                memcpy(packed+(size_t)y*row_bytes,pixels+(size_t)y*(size_t)pitch,row_bytes);
            source=packed;
        }
        unsigned format,type,internal;
        uint8_t *converted=NULL;
        if(!gl_pixel_format(bpp,rm,gm,bm,am,&format,&type,&internal)) {
            converted=rgba8_from_pixels(source,w,h,(int)row_bytes,bpp,rm,gm,bm,am);
            if(!converted) { free(packed); REJECT(8); }
            format=GL_RGBA; type=GL_UNSIGNED_BYTE; internal=GL_RGBA;
        }
        if(!g_gl_texture) p_gen_textures(1,&g_gl_texture);
        p_enable(GL_TEXTURE_2D); p_bind_texture(GL_TEXTURE_2D,g_gl_texture);
        p_tex_parameter_i(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);
        p_tex_parameter_i(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
        p_tex_parameter_i(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_REPEAT);
        p_tex_parameter_i(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_REPEAT);
        p_pixel_store_i(GL_UNPACK_ALIGNMENT,1);
        p_pixel_store_i(GL_UNPACK_ROW_LENGTH,0);
        p_tex_image_2d(GL_TEXTURE_2D,0,internal,w,h,0,format,type,converted?converted:source);
        if(getenv("RECOMP_D3D7_DIAG")) {
            static uint32_t seen[64]; static unsigned nseen, tn;
            unsigned known=0;
            for(unsigned i=0;i<nseen;i++) if(seen[i]==g_texture_surface) { known=1; break; }
            if(!known && nseen<64) {
                seen[nseen++]=g_texture_surface;
                tn++;
                const uint8_t *sample=(const uint8_t *)(converted?converted:source);
                size_t bpp2=(size_t)(bpp/8);
                size_t sstride=(converted||packed)?row_bytes:(size_t)pitch;
                uint32_t c0=0,c1=0; size_t mid=(size_t)(h/2)*sstride+(size_t)(w/2)*bpp2;
                memcpy(&c0,sample+mid,bpp2<4?bpp2:4); memcpy(&c1,sample,4);
                fprintf(stderr,"[d3d7] tex#%u %08x %dx%d bpp=%d fmt=%u,%u,%u,%u first=%08x mid=%08x\n",
                        tn,g_texture_surface,w,h,bpp,rm,gm,bm,am,c1,c0);
            }
        }
        free(converted);
        free(packed);
        p_pixel_store_i(GL_UNPACK_ROW_LENGTH,0); p_pixel_store_i(GL_UNPACK_ALIGNMENT,4);
        apply_stage0_state();
    } else if(stage0_factor_active() && !stage0_texture_active()) {
        static const uint8_t white[4]={255,255,255,255};
        if(!g_gl_texture) p_gen_textures(1,&g_gl_texture);
        if(!g_gl_texture) REJECT(8);
        p_enable(GL_TEXTURE_2D); p_bind_texture(GL_TEXTURE_2D,g_gl_texture);
        p_pixel_store_i(GL_UNPACK_ALIGNMENT,1); p_pixel_store_i(GL_UNPACK_ROW_LENGTH,0);
        p_tex_image_2d(GL_TEXTURE_2D,0,GL_RGBA,1,1,0,GL_RGBA,GL_UNSIGNED_BYTE,white);
        p_pixel_store_i(GL_UNPACK_ROW_LENGTH,0); p_pixel_store_i(GL_UNPACK_ALIGNMENT,4);
        apply_stage0_state();
    } else p_disable(GL_TEXTURE_2D);
    if(p_secondary_color3ub) {
        color_sum_was_enabled=p_is_enabled(GL_COLOR_SUM)!=0;
        if(has_secondary) p_enable(GL_COLOR_SUM); else p_disable(GL_COLOR_SUM);
    }
    if(!(fvf&0x040)) use_material_diffuse();
    p_begin(mode);
    if(idx) for(uint32_t i=0;i<index_count;i++) emit_vertex(base+(uint32_t)idx[i]*stride,fvf,(fvf&0x00e)==4);
    else for(uint32_t i=0;i<vertex_count;i++) emit_vertex(base+i*stride,fvf,(fvf&0x00e)==4);
    p_end();
    if(p_secondary_color3ub) {
        if(color_sum_was_enabled) p_enable(GL_COLOR_SUM); else p_disable(GL_COLOR_SUM);
    }
    g_draw_ok++;
    return 1;
}

static int strided_field_valid(uint32_t desc,uint32_t offset,uint32_t count,uint32_t bytes) {
    uint32_t data=MEM32(desc+offset),stride=MEM32(desc+offset+4);
    if(!data || stride<bytes || !count) return 0;
    return (uint64_t)data+(uint64_t)(count-1)*stride+bytes<=UINT32_MAX;
}

static void copy_strided_field(uint8_t *dst,uint32_t dst_stride,uint32_t dst_offset,
                               uint32_t desc,uint32_t src_offset,uint32_t count,uint32_t bytes) {
    uint32_t data=MEM32(desc+src_offset),stride=MEM32(desc+src_offset+4);
    for(uint32_t i=0;i<count;i++)
        memcpy(dst+(size_t)i*dst_stride+dst_offset,hle_ptr(data+i*stride),bytes);
}

static uint32_t pack_strided_vertices(uint32_t fvf,uint32_t desc,uint32_t count) {
    uint32_t packed_stride=vertex_stride(fvf),position=fvf&0x00eu;
    uint32_t tex_count=(fvf>>8)&15u,off=position==4?16u:12u,tex_bytes=0;
    if(!desc || desc>UINT32_MAX-40u || !count || count>1000000u || !packed_stride || packed_stride>256u || tex_count>1u) return 0;
    if(position!=2u && position!=4u) return 0;
    if(position==4u && (fvf&0x010u)) return 0;
    if(!strided_field_valid(desc,0,count,off)) return 0;
    if(fvf&0x010u) { if(!strided_field_valid(desc,8,count,12)) return 0; }
    if(fvf&0x040u) { if(!strided_field_valid(desc,16,count,4)) return 0; }
    if(fvf&0x080u) { if(!strided_field_valid(desc,24,count,4)) return 0; }
    if(tex_count) {
        uint32_t dimcode=(fvf>>16)&3u;
        tex_bytes=4u*(dimcode==3u?1u:dimcode==1u?3u:dimcode==2u?4u:2u);
        if(!strided_field_valid(desc,32,count,tex_bytes)) return 0;
    }
    size_t total=(size_t)packed_stride*count;
    uint32_t guest=hle_va(hle_alloc(total));
    if(!guest) return 0;
    uint8_t *dst=hle_ptr(guest);
    copy_strided_field(dst,packed_stride,0,desc,0,count,off);
    off=position==4u?16u:12u;
    if(fvf&0x010u) { copy_strided_field(dst,packed_stride,off,desc,8,count,12); off+=12; }
    if(fvf&0x040u) { copy_strided_field(dst,packed_stride,off,desc,16,count,4); off+=4; }
    if(fvf&0x080u) { copy_strided_field(dst,packed_stride,off,desc,24,count,4); off+=4; }
    if(tex_count) copy_strided_field(dst,packed_stride,off,desc,32,count,tex_bytes);
    return guest;
}

static void dev_draw_strided(void) {
    uint32_t primitive=A32(1),fvf=A32(2),desc=A32(3),count=A32(4),flags=A32(5);
    if(flags) RET(D3DERR_NOTAVAILABLE,6);
    uint32_t vertices=pack_strided_vertices(fvf,desc,count);
    if(!vertices) RET(D3DERR_INVALIDCALL,6);
    int ok=draw_vertices(primitive,fvf,vertices,count,0,0);
    hle_free(hle_ptr(vertices));
    RET(ok?D3D_OK:D3DERR_INVALIDCALL,6);
}

static void dev_draw_indexed_strided(void) {
    uint32_t primitive=A32(1),fvf=A32(2),desc=A32(3),count=A32(4),indices=A32(5),index_count=A32(6),flags=A32(7);
    if(flags) RET(D3DERR_NOTAVAILABLE,8);
    if(!indices || !index_count) RET(D3DERR_INVALIDCALL,8);
    uint32_t vertices=pack_strided_vertices(fvf,desc,count);
    if(!vertices) RET(D3DERR_INVALIDCALL,8);
    int ok=draw_vertices(primitive,fvf,vertices,count,indices,index_count);
    hle_free(hle_ptr(vertices));
    RET(ok?D3D_OK:D3DERR_INVALIDCALL,8);
}

static void d3d_qi(void) { uint32_t s=A32(0),iid=A32(1),out=A32(2); if(!out) RET(E_POINTER,3);
    /* A NULL iid is a pointer error; an all-zero iid is IID_IUnknown, which is
     * compared, not tested for truth. */
    if(!iid) { MEM32(out)=0; RET(E_POINTER,3); }
    const uint8_t *g=hle_ptr(iid); if(!memcmp(g,IID_IUNKNOWN,16)||!memcmp(g,IID_IDIRECT3D7,16)||!memcmp(g,IID_IDIRECT3D7_ALT,16)) { MEM32(out)=s; MEM32(s+4)++; RET(D3D_OK,3); }
    MEM32(out)=0; RET(E_NOINTERFACE,3); }
static void d3d_addref(void) { RET(++MEM32(A32(0)+4),1); }
static void d3d_release(void) { uint32_t s=A32(0),r=MEM32(s+4); if(r)r--; MEM32(s+4)=r; if(getenv("RECOMP_D3D7_DIAG")) fprintf(stderr,"[d3d7] IDirect3D7::Release %08x -> refs %u\n",s,r); if(!r){hle_com_free(s);if(g_d3d==s)g_d3d=0;} RET(r,1); }
static void dev_qi(void) { uint32_t s=A32(0),iid=A32(1),out=A32(2); if(!iid||!out) RET(E_POINTER,3); if(memcmp(hle_ptr(iid),IID_IUNKNOWN,16)&&memcmp(hle_ptr(iid),IID_IDIRECT3DDEVICE7,16)){MEM32(out)=0;RET(E_NOINTERFACE,3);} MEM32(out)=s; MEM32(s+4)++; RET(D3D_OK,3); }
static void dev_addref(void) { RET(++MEM32(A32(0)+4),1); }
static void dev_release(void) { uint32_t s=A32(0),r=MEM32(s+4); if(r)r--; MEM32(s+4)=r; if(!r){if(MEM32(s+8))hle_free(hle_ptr(MEM32(s+8)));hle_com_free(s);if(g_device==s){g_device=0;g_scene_active=0;hle_screen_d3d7_active(0);release_surface_ref(g_texture_surface);g_texture_surface=0;release_surface_ref(g_render_target);g_render_target=0;if(g_gl_texture&&load_gl())p_delete_textures(1,&g_gl_texture);g_gl_texture=0;if(g_rt_framebuffer&&load_fbo()){p_bind_framebuffer(GL_FRAMEBUFFER,0);p_delete_framebuffers(1,&g_rt_framebuffer);p_delete_textures(1,&g_rt_color);p_delete_renderbuffers(1,&g_rt_depth);}g_rt_framebuffer=g_rt_color=g_rt_depth=0;g_rt_width=g_rt_height=0;}} RET(r,1); }
static void vb_qi(void) { uint32_t s=A32(0),iid=A32(1),out=A32(2); if(!iid||!out) RET(E_POINTER,3); if(memcmp(hle_ptr(iid),IID_IUNKNOWN,16)&&memcmp(hle_ptr(iid),IID_IDIRECT3DVERTEXBUFFER7,16)){MEM32(out)=0;RET(E_NOINTERFACE,3);} MEM32(out)=s; MEM32(s+4)++; RET(D3D_OK,3); }
static void vb_addref(void) { RET(++MEM32(A32(0)+4),1); }
static void vb_release(void) { uint32_t s=A32(0),r=MEM32(s+4); if(r)r--; MEM32(s+4)=r; if(!r){if(MEM32(s+8))hle_free(hle_ptr(MEM32(s+8)));hle_com_free(s);} RET(r,1); }

static void d3d_write_prim_caps(uint32_t p, int depth_bits) {
    MEM32(p)=D3D7_PRIM_CAPS_SIZE;
    MEM32(p+4)=0x70u;                          /* cull none, CW and CCW */
    MEM32(p+8)=(depth_bits?0x30u:0x20u)|0x1u; /* dither; z test only with a depth buffer */
    MEM32(p+12)=depth_bits?0xFFu:0u;          /* all D3D compare functions */
    MEM32(p+16)=0x7FFu;                        /* blend factors, including source alpha saturate */
    MEM32(p+20)=0x3FFu;                        /* destination factors exclude source-only saturate */
    MEM32(p+24)=0xFFu;                         /* alpha compare functions */
    MEM32(p+28)=0xAu;                           /* flat and Gouraud RGB shading */
    MEM32(p+32)=0xDu;                           /* perspective, alpha, transparency */
    MEM32(p+36)=0x03000303u;                   /* point/linear min and mag filters */
    MEM32(p+40)=0x42u;                          /* texture select/copy and modulate */
    MEM32(p+44)=0x5u;                           /* wrap and clamp */
    MEM32(p+48)=MEM32(p+52)=0;
}

/* D3DDEVCAPS_HWRASTERIZATION says where triangles are rasterised. This backend
 * hands them to OpenGL, so the claim is true exactly when the GL renderer is
 * not itself software. The renderer string is a naming heuristic, not a probe:
 * the common software rasterisers name themselves, and everything else is the
 * GPU that drew the window in the first place. */
static int gl_rasterises_on_hardware(void) {
    if (!load_gl()) return 0;
    const char *r = (const char *)p_getstring(GL_RENDERER);
    if (!r) return 0;
    static const char *const software[] = {"llvmpipe","softpipe","swrast","software","swiftshader","gdi generic"};
    for (size_t k = 0; k < sizeof software / sizeof *software; k++)
        if (strcasestr(r, software[k])) return 0;
    return 1;
}

static void d3d_write_device_desc7(uint32_t p,const uint8_t *guid,int tnl) {
    int depth_bits=0, max_texture=0, max_lights=0;
    int mode_w,mode_h,mode_bpp;
    memset(hle_ptr(p),0,D3D7_DEVICE_DESC_SIZE);
    p_getint(GL_DEPTH_BITS,&depth_bits);
    p_getint(GL_MAX_TEXTURE_SIZE,&max_texture);
    p_getint(GL_MAX_LIGHTS,&max_lights);
    if(max_lights<0)max_lights=0; else if(max_lights>8)max_lights=8;
    if(max_texture<1)max_texture=1;
    hle_dd_mode(&mode_w,&mode_h,&mode_bpp);
    (void)mode_w;(void)mode_h;

    int hw = gl_rasterises_on_hardware();
    MEM32(p+0)=0x540u;                         /* system-memory TL/texture, DrawPrimitive TL */
    if (hw) MEM32(p+0)|=0x80000u;              /* D3DDEVCAPS_HWRASTERIZATION */
    if (tnl) MEM32(p+0)|=0x10000u;             /* D3DDEVCAPS_HWTRANSFORMANDLIGHT */
    d3d_write_prim_caps(p+4,depth_bits);
    d3d_write_prim_caps(p+60,depth_bits);
    MEM32(p+116)=mode_bpp==16?0x400u:mode_bpp==24?0x200u:mode_bpp>=32?0x100u:0u;
    MEM32(p+120)=depth_bits>=32?0x100u:depth_bits>=24?0x200u:depth_bits>=16?0x400u:0u;
    MEM32(p+124)=MEM32(p+128)=1;
    MEM32(p+132)=MEM32(p+136)=(uint32_t)max_texture;
    MEM32(p+140)=MEM32(p+144)=(uint32_t)max_texture;
    MEM32(p+148)=1;                            /* no anisotropic filtering */
    MEM32(p+176)=1;                            /* one supported texture coordinate set */
    MEM32(p+180)=0xFu;                         /* disable/select arg1/select arg2/modulate */
    MEM16(p+184)=1; MEM16(p+186)=1;            /* one texture stage and texture */
    MEM32(p+188)=(uint32_t)max_lights;
    float max_w=1.0e10f; memcpy(hle_ptr(p+192),&max_w,sizeof max_w);
    memcpy(hle_ptr(p+196),guid,16);
    int max_clip_planes=0; p_getint(GL_MAX_CLIP_PLANES,&max_clip_planes);
    if(max_clip_planes<0)max_clip_planes=0; else if(max_clip_planes>6)max_clip_planes=6;
    MEM16(p+212)=(uint16_t)max_clip_planes; MEM16(p+214)=0; /* clip planes and blend matrices */
    MEM32(p+216)=tnl?0x3Au:0u;                 /* material source, directional/positional lights, local viewer */
}

static void d3d_enum(void) {
    uint32_t cb=A32(1),ctx=A32(2),desc;
    static uint32_t hal_desc,hal_name,tnl_desc,tnl_name,rgb_desc,rgb_name;
    if(!cb)RET(E_POINTER,3);
    if(!hle_screen_gl_active()||!load_gl())RET(D3DERR_NOTAVAILABLE,3);
    if(!hal_desc) {
        hal_desc=hle_guest_strdup("Direct3D HAL");
        hal_name=hle_guest_strdup("Direct3D HAL");
        tnl_desc=hle_guest_strdup("Direct3D T&L HAL");
        tnl_name=hle_guest_strdup("Direct3D T&L HAL");
        rgb_desc=hle_guest_strdup("SDL OpenGL fixed-function Direct3D 7 (RGB-compatible)");
        rgb_name=hle_guest_strdup("SDL OpenGL");
    }
    desc=hle_va(hle_alloc(D3D7_DEVICE_DESC_SIZE));
    if(!desc)RET(D3DERR_NOTAVAILABLE,3);
    int hw=gl_rasterises_on_hardware();
    if(hw) {
        d3d_write_device_desc7(desc,g_d3d7_hal_guid,0);
        uint32_t args[4]={hal_desc,hal_name,desc,ctx};
        if(hle_call_guest(cb,4,args)) {           /* DDENUMRET_OK */
            /* A title that wants accelerated geometry picks the T&L identity;
             * it is the same fixed-function GL path, which transforms and
             * lights on the GPU. Enumerate it as Windows does, after the HAL. */
            d3d_write_device_desc7(desc,g_d3d7_tnlhal_guid,1);
            uint32_t args2[4]={tnl_desc,tnl_name,desc,ctx};
            hle_call_guest(cb,4,args2);
        }
    } else {
        d3d_write_device_desc7(desc,g_d3d7_gl_device_guid,0);
        uint32_t args[4]={rgb_desc,rgb_name,desc,ctx};
        hle_call_guest(cb,4,args);
    }
    fprintf(stderr,"[d3d7] EnumDevices: %s advertised, devCaps 0x%08X\n",
            hw?"HAL and T&L HAL":"RGB emulation", MEM32(desc));
    hle_free(hle_ptr(desc));
    RET(D3D_OK,3);
}

static void d3d_enum_z(void) {
    uint32_t iid=A32(1),cb=A32(2),ctx=A32(3);
    int depth_bits=0;
    if(!iid||!cb)RET(E_POINTER,4);
    if(!d3d7_device_available(hle_ptr(iid)))RET(D3DERR_NOTAVAILABLE,4);
    if(!hle_screen_gl_active()||!load_gl())RET(D3DERR_NOTAVAILABLE,4);
    p_getint(GL_DEPTH_BITS,&depth_bits);
    if(depth_bits<16)RET(D3DERR_NOTAVAILABLE,4);
    uint32_t pf=hle_va(hle_alloc(32));
    if(!pf)RET(D3DERR_NOTAVAILABLE,4);
    /* Windows enumerates every depth the device can render into, and a title
     * picks one by matching this list against its own size preference. The GL
     * context's depth buffer is usually 24 bits, but the fixed-function path
     * accepts a 16- or 32-bit request just as well, so offer all three. */
    static const uint32_t depths[3][2] = { { 16, 0x0000FFFFu }, { 24, 0x00FFFFFFu }, { 32, 0xFFFFFFFFu } };
    for (int i = 0; i < 3; i++) {
        memset(hle_ptr(pf),0,32);
        MEM32(pf)=32; MEM32(pf+4)=0x400u;       /* DDPF_ZBUFFER */
        MEM32(pf+12)=depths[i][0];
        MEM32(pf+20)=depths[i][1];
        uint32_t args[2]={pf,ctx};
        if(!hle_call_guest(cb,2,args))break;    /* DDENUMRET_CANCEL */
    }
    hle_free(hle_ptr(pf));
    RET(D3D_OK,4);
}

static void dev_get_caps(void) {
    uint32_t p=A32(1);
    if(!p)RET(E_POINTER,2);
    if(!hle_screen_gl_active()||!load_gl())RET(D3DERR_NOTAVAILABLE,2);
    const uint8_t *guid=d3d7_guid_is(g_d3d7_created_guid,g_d3d7_tnlhal_guid)?g_d3d7_tnlhal_guid:
                          d3d7_guid_is(g_d3d7_created_guid,g_d3d7_hal_guid)?g_d3d7_hal_guid:
                          g_d3d7_gl_device_guid;
    d3d_write_device_desc7(p,guid,g_d3d7_created_tnl);
    RET(D3D_OK,2);
}

static void dev_enum_textures(void) {
    uint32_t cb=A32(1),ctx=A32(2);
    if(!cb)RET(E_POINTER,3);
    if(!hle_screen_gl_active()||!load_gl())RET(D3DERR_NOTAVAILABLE,3);
    uint32_t pf=hle_va(hle_alloc(32));
    if(!pf)RET(D3DERR_NOTAVAILABLE,3);
    static const uint32_t formats[6][8]={
        {32,0x40,0,16,0xF800,0x07E0,0x001F,0},
        {32,0x41,0,16,0x7C00,0x03E0,0x001F,0x8000},
        {32,0x41,0,16,0x0F00,0x00F0,0x000F,0xF000},
        {32,0x40,0,16,0x7C00,0x03E0,0x001F,0},
        {32,0x41,0,32,0x00FF0000,0x0000FF00,0x000000FF,0xFF000000},
        {32,0x40,0,32,0x00FF0000,0x0000FF00,0x000000FF,0}
    };
    unsigned delivered=0;
    for(unsigned i=0;i<6;i++) {
        memcpy(hle_ptr(pf),formats[i],sizeof formats[i]);
        uint32_t args[2]={pf,ctx};
        delivered++;
        if(!hle_call_guest(cb,2,args))break;
    }
    fprintf(stderr,"[d3d7] EnumTextureFormats: delivered %u formats\n",delivered);
    hle_free(hle_ptr(pf));
    RET(D3D_OK,3);
}

static void d3d_evict(void) { RET(D3D_OK,1); }
static void d3d_create_device(void) {
    uint32_t guid=A32(1),target=A32(2),out=A32(3);
    uint8_t *target_pixels; int target_w,target_h,target_pitch,target_bpp;
    if(!out)RET(E_POINTER,4); MEM32(out)=0;
    if(!guid)RET(E_POINTER,4);
    if(!d3d7_device_available(hle_ptr(guid)))RET(D3DERR_NOTAVAILABLE,4);
    memcpy(g_d3d7_created_guid,hle_ptr(guid),16);
    g_d3d7_created_tnl=d3d7_guid_is(hle_ptr(guid),g_d3d7_tnlhal_guid);
    if(!target_info(target,&target_pixels,&target_w,&target_h,&target_pitch,&target_bpp))RET(D3DERR_INVALIDCALL,4);
    if(g_device && g_render_target!=target)RET(D3DERR_NOTAVAILABLE,4);
    if(!hle_screen_gl_active()||!load_gl()||!bind_render_target(target))RET(D3DERR_NOTAVAILABLE,4);
    if(!g_dev_vt) {
        static const win32hle_shim m[]={
            {"IDirect3DDevice7::QueryInterface",dev_qi},{"IDirect3DDevice7::AddRef",dev_addref},{"IDirect3DDevice7::Release",dev_release},
            {"IDirect3DDevice7::GetCaps",dev_get_caps},{"IDirect3DDevice7::EnumTextureFormats",dev_enum_textures},{"IDirect3DDevice7::BeginScene",dev_begin_scene},{"IDirect3DDevice7::EndScene",dev_end_scene},
            {"IDirect3DDevice7::GetDirect3D",dev_get_direct3d},{"IDirect3DDevice7::SetRenderTarget",dev_set_target},{"IDirect3DDevice7::GetRenderTarget",dev_get_target},{"IDirect3DDevice7::Clear",dev_clear},
            {"IDirect3DDevice7::SetTransform",dev_set_transform},{"IDirect3DDevice7::GetTransform",dev_get_transform},{"IDirect3DDevice7::SetViewport",dev_set_viewport},{"IDirect3DDevice7::MultiplyTransform",dev_multiply_transform},
            {"IDirect3DDevice7::GetViewport",dev_get_viewport},{"IDirect3DDevice7::SetMaterial",dev_set_material},{"IDirect3DDevice7::GetMaterial",dev_get_material},{"IDirect3DDevice7::SetLight",dev_set_light},
            {"IDirect3DDevice7::GetLight",dev_get_light},{"IDirect3DDevice7::SetRenderState",dev_set_render_state},{"IDirect3DDevice7::GetRenderState",dev_get_render_state},{"IDirect3DDevice7::BeginStateBlock",dev_begin_state},
            {"IDirect3DDevice7::EndStateBlock",dev_end_state},{"IDirect3DDevice7::PreLoad",dev_preload},{"IDirect3DDevice7::DrawPrimitive",dev_draw_primitive},{"IDirect3DDevice7::DrawIndexedPrimitive",dev_draw_indexed_primitive},
            {"IDirect3DDevice7::SetClipStatus",dev_set_clip},{"IDirect3DDevice7::GetClipStatus",dev_get_clip},{"IDirect3DDevice7::DrawPrimitiveStrided",dev_draw_strided},
            {"IDirect3DDevice7::DrawIndexedPrimitiveStrided",dev_draw_indexed_strided},{"IDirect3DDevice7::DrawPrimitiveVB",dev_draw_vb},{"IDirect3DDevice7::DrawIndexedPrimitiveVB",dev_draw_indexed_vb},
            {"IDirect3DDevice7::ComputeSphereVisibility",dev_sphere},{"IDirect3DDevice7::GetTexture",dev_get_texture},{"IDirect3DDevice7::SetTexture",dev_set_texture},
            {"IDirect3DDevice7::GetTextureStageState",dev_get_tss},{"IDirect3DDevice7::SetTextureStageState",dev_set_tss},{"IDirect3DDevice7::ValidateDevice",dev_validate},
            {"IDirect3DDevice7::ApplyStateBlock",dev_apply_state},{"IDirect3DDevice7::CaptureStateBlock",dev_capture_state},{"IDirect3DDevice7::DeleteStateBlock",dev_delete_state},
            {"IDirect3DDevice7::CreateStateBlock",dev_create_state},{"IDirect3DDevice7::Load",dev_load},{"IDirect3DDevice7::LightEnable",dev_light_enable},
            {"IDirect3DDevice7::GetLightEnable",dev_get_light_enable},{"IDirect3DDevice7::SetClipPlane",dev_set_clip_plane},{"IDirect3DDevice7::GetClipPlane",dev_get_clip_plane},{"IDirect3DDevice7::GetInfo",dev_get_info},{0,0}};
        g_dev_vt=hle_com_vtable(m);
    }
    if(!g_device) {
        g_device=hle_com_new(g_dev_vt,12);
        uint32_t state=hle_va(hle_alloc(4)); MEM32(g_device+8)=state;
        identity(g_world); identity(g_view); identity(g_projection); identity(g_texture_transform);
        int depth_bits=0; p_getint(GL_DEPTH_BITS,&depth_bits);
        memset(g_render,0,sizeof g_render);
        g_render[7]=depth_bits>0; g_render[8]=3; g_render[9]=2; g_render[14]=1;
        g_render[19]=2; g_render[20]=1; g_render[22]=3; g_render[23]=4; g_render[25]=8;
        g_render[37]=0x3f800000u; g_render[38]=0x3f800000u;
        g_render[53]=g_render[54]=g_render[55]=1; g_render[56]=8;
        g_render[58]=g_render[59]=0xffffffffu;
        g_render[136]=1;
        g_render[60]=0xffffffffu;
        if(g_render[7])p_enable(GL_DEPTH_TEST);else p_disable(GL_DEPTH_TEST);
        p_depth_func(GL_LEQUAL); p_blend(GL_ONE,GL_ZERO);
        p_enable(GL_CULL_FACE); p_cull(0x0404); p_front(0x0901);
        p_depth_mask(1); p_disable(GL_ALPHA_TEST); p_disable(GL_FOG); p_disable(GL_DITHER);
        g_render[137]=1; g_render[141]=1; g_render[145]=1; g_render[147]=0; g_render[148]=0;
        memset(g_material,0,sizeof g_material); memset(g_lights,0,sizeof g_lights);
        memset(g_light_valid,0,sizeof g_light_valid); memset(g_light_enabled,0,sizeof g_light_enabled);
        int max_lights=0; p_getint(GL_MAX_LIGHTS,&max_lights);
        g_max_lights=max_lights<0?0u:max_lights>8?8u:(unsigned)max_lights;
        int max_clip_planes=0; p_getint(GL_MAX_CLIP_PLANES,&max_clip_planes);
        g_max_clip_planes=max_clip_planes<0?0u:max_clip_planes>6?6u:(unsigned)max_clip_planes;
        memset(g_clip_planes,0,sizeof g_clip_planes); memset(g_clip_plane_valid,0,sizeof g_clip_plane_valid);
        for(unsigned i=0;i<6;i++)p_disable(GL_CLIP_PLANE0+i);
        memset(g_stage_state,0,sizeof g_stage_state); g_stage_state[1]=4; g_stage_state[2]=2; g_stage_state[3]=1;
        g_stage_state[4]=4; g_stage_state[5]=2; g_stage_state[6]=1;
        g_stage_state[13]=1; g_stage_state[14]=1; g_stage_state[16]=2; g_stage_state[17]=2; g_stage_state[24]=0;
        apply_texture_transform();
        g_viewport[0]=g_viewport[1]=0;g_viewport[2]=(uint32_t)target_w;g_viewport[3]=(uint32_t)target_h;g_viewport[4]=0;g_viewport[5]=0x3f800000u;
        set_target_viewport(target,0,0,target_w,target_h);
        p_depth_range(0.0,1.0);
        float ambient[4]={0,0,0,1}; p_light_modelfv(GL_LIGHT_MODEL_AMBIENT,ambient);
        p_light_modeli(GL_LIGHT_MODEL_LOCAL_VIEWER,0); p_enable(GL_LIGHTING);
        apply_color_material_state();
        apply_material();
        g_render_target=target; MEM32(target+4)++;
    } else MEM32(g_device+4)++;
    hle_screen_d3d7_active(hle_dd_surface_is_primary_3d_target(g_render_target));
    MEM32(out)=g_device; RET(D3D_OK,4);
}

static void d3d_create_vb(void) {
    uint32_t desc=A32(1),out=A32(2); if(!out)RET(E_POINTER,4); MEM32(out)=0;
    if(!desc)RET(E_POINTER,4);
    uint32_t fvf=MEM32(desc+8), count=MEM32(desc+12), stride=vertex_stride(fvf);
    if(getenv("RECOMP_D3D7_DIAG")) fprintf(stderr,"[d3d7] CreateVertexBuffer desc=%08x fvf=%08x count=%u stride=%u\n",desc,fvf,count,stride);
    if(!stride||!count||count>0x100000u||stride>0x100u||count>0x7fffffffu/stride)RET(D3DERR_INVALIDCALL,4);
    if(!g_vb_vt) { static const win32hle_shim m[]={{"IDirect3DVertexBuffer7::QueryInterface",vb_qi},{"IDirect3DVertexBuffer7::AddRef",vb_addref},{"IDirect3DVertexBuffer7::Release",vb_release},{"IDirect3DVertexBuffer7::Lock",vb_lock},{"IDirect3DVertexBuffer7::Unlock",vb_unlock},{"IDirect3DVertexBuffer7::ProcessVertices",vb_process_vertices},{"IDirect3DVertexBuffer7::GetVertexBufferDesc",vb_get_desc},{"IDirect3DVertexBuffer7::Optimize",vb_optimize},{"IDirect3DVertexBuffer7::ProcessVerticesStrided",vb_process_strided},{0,0}}; g_vb_vt=hle_com_vtable(m); }
    uint32_t bytes=count*stride, data=hle_va(hle_alloc(bytes)); uint32_t vb=hle_com_new(g_vb_vt,20);
    MEM32(vb+8)=data; MEM32(vb+12)=bytes; MEM32(vb+16)=fvf; MEM32(out)=vb; RET(D3D_OK,4);
}

static void vb_lock(void) { uint32_t s=A32(0),out=A32(2),sz=A32(3); if(!out)RET(E_POINTER,4); MEM32(out)=MEM32(s+8);if(sz)MEM32(sz)=MEM32(s+12);RET(D3D_OK,4); }
static void vb_unlock(void) { RET(D3D_OK,1); }
static void vb_get_desc(void) { uint32_t s=A32(0),d=A32(1);if(!d)RET(E_POINTER,2);memset(hle_ptr(d),0,16);MEM32(d)=16;MEM32(d+8)=MEM32(s+16);MEM32(d+12)=MEM32(s+12)/vertex_stride(MEM32(s+16));RET(D3D_OK,2); }
static void release_surface_ref(uint32_t surface) {
    if (!surface) return;
    uint32_t vt=MEM32(surface), release=vt?MEM32(vt+8):0, args[1]={surface};
    if (release) hle_call_guest(release,1,args);
}
static void dev_set_target(void) {
    uint32_t surface=A32(1),flags=A32(2);
    uint8_t *pixels; int w,h,pitch,bpp;
    if(flags || !target_info(surface,&pixels,&w,&h,&pitch,&bpp) || g_scene_active) RET(D3DERR_INVALIDCALL,3);
    if(getenv("RECOMP_D3D7_DIAG")) {
        static unsigned st;
        if(st<40) { st++; fprintf(stderr,"[d3d7] SetRenderTarget(%08x) %dx%d bpp=%d\n",surface,w,h,bpp); }
    }
    if(surface==g_render_target) RET(D3D_OK,3);
    if(!hle_screen_gl_active()) RET(D3DERR_NOTAVAILABLE,3);
    if(!load_gl()) RET(D3DERR_NOTAVAILABLE,3);
    int old_viewport[4]; p_getint(GL_VIEWPORT,old_viewport);
    if(!bind_render_target(surface)) {
        if(g_render_target) bind_render_target(g_render_target);
        p_gl_viewport(old_viewport[0],old_viewport[1],old_viewport[2],old_viewport[3]);
        RET(D3DERR_NOTAVAILABLE,3);
    }
    int depth_bits=0; p_getint(GL_DEPTH_BITS,&depth_bits);
    if(g_render[7] && depth_bits<=0) {
        if(g_render_target) bind_render_target(g_render_target);
        p_gl_viewport(old_viewport[0],old_viewport[1],old_viewport[2],old_viewport[3]);
        RET(D3DERR_NOTAVAILABLE,3);
    }
    set_target_viewport(surface,0,0,w,h);
    g_viewport[0]=g_viewport[1]=0; g_viewport[2]=(uint32_t)w; g_viewport[3]=(uint32_t)h;
    float min_z,max_z; memcpy(&min_z,&g_viewport[4],4); memcpy(&max_z,&g_viewport[5],4);
    p_depth_range((double)min_z,(double)max_z);
    MEM32(surface+4)++;
    uint32_t old=g_render_target;
    g_render_target=surface;
    release_surface_ref(old);
    hle_screen_d3d7_active(hle_dd_surface_is_primary_3d_target(surface));
    RET(D3D_OK,3);
}
static void dev_get_target(void) {
    uint32_t out=A32(1);
    if(!out) RET(E_POINTER,2);
    MEM32(out)=g_render_target;
    if(g_render_target) MEM32(g_render_target+4)++;
    RET(D3D_OK,2);
}
static int stage0_uses_texture(uint32_t op,uint32_t arg1,uint32_t arg2) {
    arg1&=D3DTA_ARGUMENT_MASK; arg2&=D3DTA_ARGUMENT_MASK;
    if(op==2) return arg1==2;
    if(op==3) return arg2==2;
    if(op==4) return arg1==2 || arg2==2;
    return 0;
}
static int stage0_uses_factor(uint32_t op,uint32_t arg1,uint32_t arg2) {
    arg1&=D3DTA_ARGUMENT_MASK; arg2&=D3DTA_ARGUMENT_MASK;
    if(op==2) return arg1==3;
    if(op==3) return arg2==3;
    if(op==4) return arg1==3 || arg2==3;
    return 0;
}
static int stage0_texture_active(void) {
    return stage0_uses_texture(g_stage_state[1],g_stage_state[2],g_stage_state[3]) ||
           stage0_uses_texture(g_stage_state[4],g_stage_state[5],g_stage_state[6]);
}
static int stage0_factor_active(void) {
    return stage0_uses_factor(g_stage_state[1],g_stage_state[2],g_stage_state[3]) ||
           stage0_uses_factor(g_stage_state[4],g_stage_state[5],g_stage_state[6]);
}
static unsigned stage0_source(uint32_t arg) {
    arg&=D3DTA_ARGUMENT_MASK;
    return arg==3?GL_CONSTANT:arg==2?GL_TEXTURE:arg==0?GL_PRIMARY_COLOR:GL_PREVIOUS;
}
static int stage0_arg_valid(uint32_t arg) {
    return !(arg&~(D3DTA_ARGUMENT_MASK|D3DTA_MODIFIER_MASK)) &&
           (arg&D3DTA_ARGUMENT_MASK)<=3u;
}
static unsigned stage0_operand(uint32_t arg,int alpha_channel) {
    int alpha_replicate=!alpha_channel && (arg&D3DTA_ALPHAREPLICATE);
    int complement=(arg&D3DTA_COMPLEMENT)!=0;
    if(alpha_channel || alpha_replicate)
        return complement?GL_ONE_MINUS_SRC_ALPHA:GL_SRC_ALPHA;
    return complement?GL_ONE_MINUS_SRC_COLOR:GL_SRC_COLOR;
}
static void apply_stage_channel(uint32_t op,uint32_t arg1,uint32_t arg2,
                                unsigned combine,unsigned source0,unsigned source1,
                                unsigned operand0,unsigned operand1) {
    unsigned mode=op==4?GL_MODULATE:GL_REPLACE;
    uint32_t first=op==1?0u:op==2?arg1:arg2;
    uint32_t second=arg1;
    unsigned src0=op==1?GL_PREVIOUS:stage0_source(first);
    unsigned src1=stage0_source(second);
    p_tex_envi(GL_TEXTURE_ENV,combine,(int)mode);
    p_tex_envi(GL_TEXTURE_ENV,source0,(int)src0);
    p_tex_envi(GL_TEXTURE_ENV,source1,(int)src1);
    int alpha_channel=combine==GL_COMBINE_ALPHA;
    p_tex_envi(GL_TEXTURE_ENV,operand0,(int)stage0_operand(first,alpha_channel));
    p_tex_envi(GL_TEXTURE_ENV,operand1,(int)stage0_operand(second,alpha_channel));
}
static int gl_filter(uint32_t value) { return value==1?GL_NEAREST:GL_LINEAR; }
static int gl_address(uint32_t value) {
    switch(value) {
    case 2: return GL_MIRRORED_REPEAT;          /* MIRROR */
    case 3: case 4: return GL_CLAMP_TO_EDGE;    /* CLAMP; BORDER approximated */
    case 5: return GL_MIRRORED_REPEAT;          /* MIRRORONCE approximated */
    default: return GL_REPEAT;                  /* WRAP */
    }
}
static void apply_stage0_state(void) {
    if (!load_gl()) return;
    apply_texture_transform();
    uint32_t factor=g_render[60];
    float color[4]={(float)((factor>>16)&255u)/255.f,(float)((factor>>8)&255u)/255.f,
                    (float)(factor&255u)/255.f,(float)(factor>>24)/255.f};
    p_tex_envfv(GL_TEXTURE_ENV,GL_TEXTURE_ENV_COLOR,color);
    if (!g_gl_texture) return;
    p_bind_texture(GL_TEXTURE_2D,g_gl_texture);
    p_tex_parameter_i(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,gl_filter(g_stage_state[17]));
    p_tex_parameter_i(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,gl_filter(g_stage_state[16]));
    p_tex_parameter_i(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,gl_address(g_stage_state[13]));
    p_tex_parameter_i(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,gl_address(g_stage_state[14]));
    p_tex_envi(GL_TEXTURE_ENV,GL_TEXTURE_ENV_MODE,GL_COMBINE);
    apply_stage_channel(g_stage_state[1],g_stage_state[2],g_stage_state[3],GL_COMBINE_RGB,
                        GL_SOURCE0_RGB,GL_SOURCE1_RGB,GL_OPERAND0_RGB,GL_OPERAND1_RGB);
    apply_stage_channel(g_stage_state[4],g_stage_state[5],g_stage_state[6],GL_COMBINE_ALPHA,
                        GL_SOURCE0_ALPHA,GL_SOURCE1_ALPHA,GL_OPERAND0_ALPHA,GL_OPERAND1_ALPHA);
}
static void dev_get_texture(void) {
    uint32_t stage=A32(1),out=A32(2);
    if (!out) RET(E_POINTER,3);
    MEM32(out)=0;
    if (stage) RET(D3DERR_NOTAVAILABLE,3);
    if (g_texture_surface) { MEM32(g_texture_surface+4)++; MEM32(out)=g_texture_surface; }
    RET(D3D_OK,3);
}
static void dev_set_texture(void) {
    uint32_t stage=A32(1),surface=A32(2);
    if(getenv("RECOMP_D3D7_DIAG")) {
        static unsigned st0,st1;
        if(stage==1 && surface && st1<40) {
            st1++; uint8_t *px; int w=0,h=0,p=0,b=0; uint32_t f=0,m=0;
            if(hle_dd_surface_pixels(surface,&px,&w,&h,&p,&b) && px && w>0 && h>0) {
                f=read_px(px,b); m=read_px(px+(size_t)(h/2)*(size_t)p+(size_t)(w/2)*(size_t)(b/8),b);
            }
            fprintf(stderr,"[d3d7] SetTexture(stage=1,%08x) %dx%d bpp=%d first=%08x mid=%08x\n",surface,w,h,b,f,m);
        } else if(stage==0 && surface && st0<40) {
            st0++; fprintf(stderr,"[d3d7] SetTexture(stage=0,%08x)\n",surface);
        }
    }
    if (stage) RET(D3DERR_NOTAVAILABLE,3);
    if (surface) {
        uint8_t *pixels; int w,h,pitch,bpp;
        if (!hle_dd_surface_is_texture(surface) ||
            !hle_dd_surface_pixels(surface,&pixels,&w,&h,&pitch,&bpp)) RET(D3DERR_INVALIDCALL,3);
        if(w<=0 || h<=0 || (bpp!=16 && bpp!=24 && bpp!=32))
            RET(D3DERR_NOTAVAILABLE,3);
        size_t bytes_per_pixel=(size_t)bpp/8u;
        if(pitch<=0 || (size_t)w>SIZE_MAX/bytes_per_pixel ||
           (size_t)pitch<(size_t)w*bytes_per_pixel) RET(D3DERR_NOTAVAILABLE,3);
        (void)pixels;
    }
    if (surface==g_texture_surface) RET(D3D_OK,3);
    uint32_t old=g_texture_surface;
    g_texture_surface=surface;
    if (surface) MEM32(surface+4)++;
    release_surface_ref(old);
    if (!surface && load_gl()) p_disable(GL_TEXTURE_2D);
    RET(D3D_OK,3);
}
static void dev_get_tss(void) {
    uint32_t stage=A32(1),state=A32(2),out=A32(3);
    if (!out) RET(E_POINTER,4);
    if (stage>7) RET(D3DERR_INVALIDCALL,4);
    /* Only stage 0 has renderer backing; higher stages read back as unset. */
    MEM32(out)=stage?0:(state<32u?g_stage_state[state]:0);
    RET(D3D_OK,4);
}
static void dev_set_tss(void) {
    uint32_t stage=A32(1),state=A32(2),value=A32(3);
    uint32_t err=D3D_OK;
    if (stage>7) err=D3DERR_INVALIDCALL;
    else switch(state) {
    case 1: case 4:                              /* COLOROP / ALPHAOP */
        if (value<1 || value>4) err=D3DERR_NOTAVAILABLE;
        break;
    case 2: case 3: case 5: case 6:               /* colour/alpha args */
        if (!stage0_arg_valid(value)) err=D3DERR_NOTAVAILABLE;
        break;
    case 11: {                                   /* TEXCOORDINDEX */
        uint32_t set=value&0xFFFFu;
        if (value&0xFFFF0000u) err=D3DERR_NOTAVAILABLE;  /* generated coords */
        else if (set>=8u) err=D3DERR_INVALIDCALL;
        break; }
    case 12:                                     /* ADDRESS (legacy, both axes) */
        if (value<1 || value>5) err=D3DERR_INVALIDCALL;
        break;
    case 13: case 14: case 25:                   /* ADDRESSU / ADDRESSV / W */
        if (value<1 || value>5) err=D3DERR_INVALIDCALL;
        break;
    case 15:                                     /* BORDERCOLOR */
        break;
    case 16: case 17:                            /* MAG / MIN filter */
        if (value<1 || value>3) err=D3DERR_NOTAVAILABLE;
        break;
    case 18:                                     /* MIP filter */
        if (value>2) err=D3DERR_NOTAVAILABLE;
        break;
    case 19: case 22: case 23: {                 /* float-valued states */
        float f; memcpy(&f,&value,4);
        if (!isfinite(f)) err=D3DERR_INVALIDCALL;
        break; }
    case 20:                                     /* MAXMIPLEVEL */
        if (value>15) err=D3DERR_INVALIDCALL;
        break;
    case 21:                                     /* MAXANISOTROPY */
        if (value<1 || value>16) err=D3DERR_INVALIDCALL;
        break;
    case 24: {                                   /* TEXTURETRANSFORMFLAGS */
        uint32_t count=value&7u;
        if(value&~0x107u) err=D3DERR_INVALIDCALL;
        else if(count>4u || (count==0u && value!=0u) || ((value&0x100u) && count<2u)) err=D3DERR_INVALIDCALL;
        break; }
    case 26: case 27:                            /* COLORARG0 / ALPHAARG0 */
        if (!stage0_arg_valid(value)) err=D3DERR_NOTAVAILABLE;
        break;
    case 28:                                     /* RESULTARG */
        if (value>1) err=D3DERR_NOTAVAILABLE;
        break;
    default:
        err=D3DERR_NOTAVAILABLE;
        break;
    }
    if (getenv("RECOMP_D3D7_DIAG")) {
        static unsigned traced;
        if (err || traced<160) fprintf(stderr,"[d3d7] SetTextureStageState(%u,%u,0x%x) -> %08x\n",stage,state,value,err);
        if (!err) traced++;
    }
    if (err) RET(err,4);
    if (!stage) {
        if (state==12) g_stage_state[13]=g_stage_state[14]=value;
        else g_stage_state[state]=value;
        if (state==1 || state==2 || state==3 || state==4 || state==5 || state==6 || state==11 ||
            state==12 || state==13 || state==14 || state==16 || state==17 || state==18 || state==24)
            apply_stage0_state();
    }
    RET(D3D_OK,4);
}
static void dev_validate(void) {
    uint32_t passes=A32(1);
    if(!passes) RET(E_POINTER,2);
    MEM32(passes)=0;
    if(!load_gl()) RET(D3DERR_NOTAVAILABLE,2);
    for(unsigned op_index=0;op_index<2;op_index++) {
        unsigned base=op_index?4u:1u;
        uint32_t op=g_stage_state[base],arg1=g_stage_state[base+1],arg2=g_stage_state[base+2];
        if(op<1 || op>4 || !stage0_arg_valid(arg1) || !stage0_arg_valid(arg2)) RET(D3DERR_NOTAVAILABLE,2);
    }
    MEM32(passes)=1;
    RET(D3D_OK,2);
}
static void dev_get_direct3d(void) { uint32_t out=A32(1);if(!out)RET(E_POINTER,2);MEM32(out)=g_d3d;MEM32(g_d3d+4)++;RET(D3D_OK,2); }
static void dev_begin_scene(void) {
    if(g_scene_active) RET(D3DERR_INVALIDCALL,1);
    if(!load_gl()) RET(D3DERR_NOTAVAILABLE,1);
    g_scene_active=1;
    RET(D3D_OK,1);
}
static void dev_end_scene(void) {
    if(!g_scene_active) RET(D3DERR_INVALIDCALL,1);
    g_scene_active=0;
    int prim=hle_dd_surface_is_primary_3d_target(g_render_target);
    int stored=store_render_target(g_render_target);
    static unsigned diag_frames;
    unsigned diag_show = getenv("RECOMP_D3D7_DIAG") && diag_frames>=30 && ((diag_frames-30)%120)==0;
    diag_frames++;
    if(diag_show) { int w=0,h=0,p=0,b=0; uint8_t *px=NULL; hle_dd_surface_pixels(g_render_target,&px,&w,&h,&p,&b);
        unsigned nz=0, n=0; uint32_t center=0;
        if(px) for(int y=0;y<h;y+=8) for(int x=0;x<w;x+=8) { uint32_t v=read_px(px+(size_t)y*(size_t)p+(size_t)x*(size_t)(b/8),b); nz+=v!=0; n++; if(x==w/2&&y==h/2) center=v; }
        fprintf(stderr,"[diag] EndScene target=%08x primary=%d stored=%d px=%p %dx%d nz=%u/%u center=%08x\n",g_render_target,prim,stored,(void*)px,w,h,nz,n,center);
        fprintf(stderr,"[diag] draws calls=%u ok=%u rej=[%u,%u,%u,%u,%u,%u,%u,%u] tex=%08x fvf=%08x vp=%d,%d,%d,%d\n",
            g_draw_calls,g_draw_ok,g_draw_reject[0],g_draw_reject[1],g_draw_reject[2],g_draw_reject[3],
            g_draw_reject[4],g_draw_reject[5],g_draw_reject[6],g_draw_reject[7],g_texture_surface,g_last_fvf,
            (int32_t)g_viewport[0],(int32_t)g_viewport[1],(int32_t)g_viewport[2],(int32_t)g_viewport[3]);
        { unsigned lights=0; for(unsigned i=0;i<g_max_lights&&i<8;i++) if(g_light_enabled[i]) lights++;
          uint32_t md=0,ma=0; memcpy(&md,g_material,4); memcpy(&ma,g_material+16,4);
          fprintf(stderr,"[diag] light=%u lights=%u cmat=%u csrc=%u/%u mdiff=%08x mamb=%08x amb=%08x\n",
                  g_render[137],lights,g_render[141],g_render[145],g_render[147],md,ma,g_render[139]); }
        { float fs,fe,fd; memcpy(&fs,&g_render[36],4); memcpy(&fe,&g_render[37],4); memcpy(&fd,&g_render[38],4);
          fprintf(stderr,"[diag] fog=%u mode=%u/%u color=%08x start=%g end=%g dens=%g\n",
                  g_render[28],g_render[35],g_render[140],g_render[34],fs,fe,fd); }
        fprintf(stderr,"[diag] tss0 op=%u a1=%u a2=%u tci=%u addr=%u/%u filt=%u/%u/%u\n",
                g_stage_state[1],g_stage_state[2],g_stage_state[3],g_stage_state[11],
                g_stage_state[13],g_stage_state[14],g_stage_state[16],g_stage_state[17],g_stage_state[18]);
        fprintf(stderr,"[diag] lit=%u unlit=%u fvfs=",g_lit_draws,g_unlit_draws);
        for(unsigned i=0;i<8;i++) if(g_fvf_hist_n[i]) fprintf(stderr," %08x:%u",g_fvf_hist_key[i],g_fvf_hist_n[i]);
        fprintf(stderr,"\n");
        for(unsigned i=0;i<32;i++) if(g_tex_hist_n[i]) {
            uint8_t *px=NULL; int w=0,h=0,p=0,b=0; uint32_t f=0,m=0;
            if(hle_dd_surface_pixels(g_tex_hist_tex[i],&px,&w,&h,&p,&b)&&px&&w>0&&h>0) {
                f=read_px(px,b); m=read_px(px+(size_t)(h/2)*(size_t)p+(size_t)(w/2)*(size_t)(b/8),b);
            }
            fprintf(stderr,"[diag] draw tex=%08x fvf=%08x n=%u %dx%d bpp=%d first=%08x mid=%08x\n",
                    g_tex_hist_tex[i],g_tex_hist_fvf[i],g_tex_hist_n[i],w,h,b,f,m);
        }
        for(unsigned i=0;i<16;i++) if(g_mt_n[i]) {
            uint8_t *px=NULL; int w=0,h=0,p=0,b=0; uint32_t f=0,m=0;
            if(hle_dd_surface_pixels(g_mt_tex[i],&px,&w,&h,&p,&b)&&px&&w>0&&h>0) {
                f=read_px(px,b); m=read_px(px+(size_t)(h/2)*(size_t)p+(size_t)(w/2)*(size_t)(b/8),b);
            }
            fprintf(stderr,"[diag] multi tex=%08x n=%u %dx%d bpp=%d first=%08x mid=%08x\n",
                    g_mt_tex[i],g_mt_n[i],w,h,b,f,m);
        }
    }
    memset(g_fvf_hist_n,0,sizeof g_fvf_hist_n); memset(g_fvf_hist_key,0,sizeof g_fvf_hist_key);
    memset(g_mt_n,0,sizeof g_mt_n); memset(g_mt_tex,0,sizeof g_mt_tex);
    memset(g_tex_hist_n,0,sizeof g_tex_hist_n); memset(g_tex_hist_tex,0,sizeof g_tex_hist_tex); memset(g_tex_hist_fvf,0,sizeof g_tex_hist_fvf);
    g_lit_draws=g_unlit_draws=0;
    memset(g_draw_reject,0,sizeof g_draw_reject); g_draw_calls=g_draw_ok=0;
    if(!stored) RET(D3DERR_NOTAVAILABLE,1);
    if(prim) hle_screen_gl_swap();
    RET(D3D_OK,1);
}
static void dev_clear(void) {
    uint32_t count=A32(1),rects=A32(2),flags=A32(3),c=A32(4),zb=A32(5),stencil=A32(6);
    float z,old_clear_color[4],old_clear_depth; int depth_bits=0,stencil_bits=0,glvp[4],old_scissor[4];
    int old_color_mask[4],old_depth_mask,old_stencil_mask,old_clear_stencil;
    unsigned mask=0; int scissor_was_enabled;
    int64_t vx=(int64_t)(int32_t)g_viewport[0],vy=(int64_t)(int32_t)g_viewport[1];
    int64_t vw=(int64_t)g_viewport[2],vh=(int64_t)g_viewport[3];
    if(!flags || (flags&~7u) || count>4096 || ((count==0)!=(rects==0)) ||
       (count && rects>UINT32_MAX-count*16u) || vw<=0 || vh<=0)
        RET(D3DERR_INVALIDCALL,7);
    if(!load_gl()) RET(D3DERR_NOTAVAILABLE,7);
    p_getint(GL_DEPTH_BITS,&depth_bits); p_getint(GL_STENCIL_BITS,&stencil_bits);
    if((flags&2) && depth_bits<=0) RET(D3DERR_INVALIDCALL,7);
    if((flags&4) && stencil_bits<=0) RET(D3DERR_INVALIDCALL,7);
    p_getint(GL_VIEWPORT,glvp);
    if(glvp[2]<=0 || glvp[3]<=0) RET(D3DERR_INVALIDCALL,7);
    p_getint(GL_COLOR_WRITEMASK,old_color_mask); p_getint(GL_DEPTH_WRITEMASK,&old_depth_mask);
    p_getint(GL_STENCIL_WRITEMASK,&old_stencil_mask);
    scissor_was_enabled=p_is_enabled(GL_SCISSOR_TEST)!=0;
    p_getint(GL_SCISSOR_BOX,old_scissor);
    if(flags&1) { p_getfloat(GL_COLOR_CLEAR_VALUE,old_clear_color); p_clear_color((float)((c>>16)&255)/255.f,(float)((c>>8)&255)/255.f,(float)(c&255)/255.f,(float)(c>>24)/255.f); mask|=GL_COLOR_BUFFER_BIT; }
    if(flags&2) { p_getfloat(GL_DEPTH_CLEAR_VALUE,&old_clear_depth); memcpy(&z,&zb,sizeof z); p_clear_depth((double)z); mask|=GL_DEPTH_BUFFER_BIT; }
    if(flags&4) { p_getint(GL_STENCIL_CLEAR_VALUE,&old_clear_stencil); p_clear_stencil((int)stencil); mask|=GL_STENCIL_BUFFER_BIT; }
    if(flags&1) p_color_mask(1,1,1,1);
    if(flags&2) p_depth_mask(1);
    if(flags&4) p_stencil_mask(~0u);
    p_enable(GL_SCISSOR_TEST);
    for(uint32_t i=0;i<(count?count:1);i++) {
        int64_t left=vx,top=vy,right=vx+vw,bottom=vy+vh;
        if(count) {
            uint32_t r=rects+i*16u;
            int32_t x1=(int32_t)MEM32(r),y1=(int32_t)MEM32(r+4),x2=(int32_t)MEM32(r+8),y2=(int32_t)MEM32(r+12);
            if(x1>left)left=x1; if(y1>top)top=y1;
            if(x2<right)right=x2; if(y2<bottom)bottom=y2;
        }
        if(right<=left || bottom<=top) continue;
        int64_t rel_left=left-vx,rel_top=top-vy,rel_right=right-vx,rel_bottom=bottom-vy;
        int64_t sx0=(rel_left*glvp[2])/vw;
        int64_t sx1=(rel_right*glvp[2]+vw-1)/vw;
        int64_t sy0=((vh-rel_bottom)*glvp[3])/vh;
        int64_t sy1=((vh-rel_top)*glvp[3]+vh-1)/vh;
        p_scissor(glvp[0]+(int)sx0,glvp[1]+(int)sy0,(int)(sx1-sx0),(int)(sy1-sy0));
        p_clear(mask);
    }
    if(flags&1) p_color_mask((unsigned char)old_color_mask[0],(unsigned char)old_color_mask[1],(unsigned char)old_color_mask[2],(unsigned char)old_color_mask[3]);
    if(flags&2) p_depth_mask((unsigned char)old_depth_mask);
    if(flags&4) p_stencil_mask((unsigned)old_stencil_mask);
    if(flags&1) p_clear_color(old_clear_color[0],old_clear_color[1],old_clear_color[2],old_clear_color[3]);
    if(flags&2) p_clear_depth((double)old_clear_depth);
    if(flags&4) p_clear_stencil(old_clear_stencil);
    p_scissor(old_scissor[0],old_scissor[1],old_scissor[2],old_scissor[3]);
    if(!scissor_was_enabled) p_disable(GL_SCISSOR_TEST);
    RET(D3D_OK,7);
}

static float *transform_for(uint32_t state) { if(state==1)return g_world;if(state==2)return g_view;if(state==3)return g_projection;if(state==16)return g_texture_transform;return NULL; }
static void dev_set_transform(void) { uint32_t st=A32(1),m=A32(2);float *to=transform_for(st);if(!to||!m)RET(D3DERR_INVALIDCALL,3);memcpy(to,hle_ptr(m),64);if(st==1||st==2){refresh_modelview();if(st==2){refresh_lights();refresh_clip_planes();}}if(st==3)load_projection();if(st==16)apply_texture_transform();RET(D3D_OK,3); }
static void dev_get_transform(void) { uint32_t st=A32(1),m=A32(2);float *from=transform_for(st);if(!from||!m)RET(D3DERR_INVALIDCALL,3);memcpy(hle_ptr(m),from,64);RET(D3D_OK,3); }
static void dev_multiply_transform(void) { uint32_t st=A32(1),m=A32(2);float *to=transform_for(st);if(!to||!m)RET(D3DERR_INVALIDCALL,3);float a[16],b[16],r[16];memcpy(a,to,64);memcpy(b,hle_ptr(m),64);for(int i=0;i<4;i++)for(int j=0;j<4;j++){r[i*4+j]=0;for(int k=0;k<4;k++)r[i*4+j]+=a[i*4+k]*b[k*4+j];}memcpy(to,r,64);if(st==1||st==2){refresh_modelview();if(st==2){refresh_lights();refresh_clip_planes();}}if(st==3)load_projection();if(st==16)apply_texture_transform();RET(D3D_OK,3); }
static void dev_set_viewport(void) {
    uint32_t p=A32(1),x,y,w,h; uint8_t *pixels; int target_w,target_h,pitch,bpp; float min_z,max_z;
    if(!p) RET(E_POINTER,2);
    x=MEM32(p);y=MEM32(p+4);w=MEM32(p+8);h=MEM32(p+12);
    memcpy(&min_z,hle_ptr(p+16),sizeof min_z);memcpy(&max_z,hle_ptr(p+20),sizeof max_z);
    if(!target_info(g_render_target,&pixels,&target_w,&target_h,&pitch,&bpp)) RET(D3DERR_NOTAVAILABLE,2);
    if(!w || !h || (uint64_t)x+w>(uint32_t)target_w || (uint64_t)y+h>(uint32_t)target_h ||
       !(min_z>=0.f && max_z<=1.f && min_z<=max_z)) RET(D3DERR_INVALIDCALL,2);
    if(!load_gl()) RET(D3DERR_NOTAVAILABLE,2);
    for(int i=0;i<6;i++)g_viewport[i]=MEM32(p+i*4);
    set_target_viewport(g_render_target,(int)x,(int)y,(int)w,(int)h);
    p_depth_range((double)min_z,(double)max_z);
    RET(D3D_OK,2);
}
static void dev_get_viewport(void) { uint32_t p=A32(1);if(!p)RET(E_POINTER,2);for(int i=0;i<6;i++)MEM32(p+i*4)=g_viewport[i];RET(D3D_OK,2); }

/* D3DMATERIAL7 is four RGBA float vectors followed by power (68 bytes).
 * D3DLIGHT7 is type, three RGBA vectors, two xyz vectors and seven floats
 * (104 bytes). Keep the exact guest layouts for Get* and only copy from guest
 * memory after validating their pointers. */
static void apply_material(void) {
    if (!load_gl()) return;
    float m[17],no_specular[4]={0,0,0,0}; memcpy(m,g_material,sizeof m);
    p_materialfv(GL_FRONT_AND_BACK,GL_DIFFUSE,m);
    p_materialfv(GL_FRONT_AND_BACK,GL_AMBIENT,m+4);
    p_materialfv(GL_FRONT_AND_BACK,GL_SPECULAR,g_render[29]?m+8:no_specular);
    p_materialfv(GL_FRONT_AND_BACK,GL_EMISSION,m+12);
    p_materialf(GL_FRONT_AND_BACK,GL_SHININESS,m[16]);
}
static void apply_light(unsigned index) {
    const uint8_t *l=g_lights[index];
    unsigned gl=GL_LIGHT0+index;
    uint32_t type; memcpy(&type,l,4);
    float diff[4],spec[4],amb[4],pos[4],dir[3],range,falloff,a0,a1,a2,theta,phi;
    memcpy(diff,l+4,16);memcpy(spec,l+20,16);memcpy(amb,l+36,16);
    memcpy(pos,l+52,12); pos[3]=(type==3)?0.f:1.f;
    memcpy(dir,l+64,12);
    if(type==3) for(int i=0;i<3;i++)pos[i]=-dir[i];
    memcpy(&range,l+76,4); memcpy(&falloff,l+80,4);
    memcpy(&a0,l+84,4); memcpy(&a1,l+88,4); memcpy(&a2,l+92,4);
    memcpy(&theta,l+96,4); memcpy(&phi,l+100,4);
    /* D3D7 stores lights in world space. OpenGL captures light positions in
     * eye space when submitted, so use the view matrix without the world
     * transform, then restore the vertex modelview matrix. */
    p_matrix_mode(GL_MODELVIEW); p_load_matrix(g_view);
    p_lightfv(gl,GL_DIFFUSE,diff); p_lightfv(gl,GL_SPECULAR,spec); p_lightfv(gl,GL_AMBIENT,amb);
    p_lightfv(gl,GL_POSITION,pos);
    if(type==2) {
        p_lightfv(gl,GL_SPOT_DIRECTION,dir);
        p_lightf(gl,GL_SPOT_CUTOFF,phi>=3.14159265f?180.f:phi*(90.f/3.14159265f));
        p_lightf(gl,GL_SPOT_EXPONENT,falloff<0.f?0.f:falloff>128.f?128.f:falloff);
    } else { p_lightf(gl,GL_SPOT_CUTOFF,180.f); }
    p_lightf(gl,GL_CONSTANT_ATTENUATION,a0);
    p_lightf(gl,GL_LINEAR_ATTENUATION,a1);
    p_lightf(gl,GL_QUADRATIC_ATTENUATION,a2);
    (void)range; (void)theta; /* GL's fixed pipeline has no D3D range/inner-cone equivalent. */
    if(g_light_enabled[index])p_enable(gl);else p_disable(gl);
    refresh_modelview();
}
static void refresh_lights(void) {
    for(unsigned i=0;i<g_max_lights&&i<8;i++)if(g_light_valid[i])apply_light(i);
}
static void refresh_clip_planes(void) {
    if(!load_gl() || !g_max_clip_planes) return;
    p_matrix_mode(GL_MODELVIEW); p_load_matrix(g_view);
    for(unsigned i=0;i<g_max_clip_planes;i++) if(g_clip_plane_valid[i]) {
        double equation[4];
        for(unsigned j=0;j<4;j++) equation[j]=(double)g_clip_planes[i][j];
        p_clip_plane(GL_CLIP_PLANE0+i,equation);
    }
    refresh_modelview();
}
/* A HAL rasteriser clips against the viewport and scissor; the clip status a
 * title sets is a reporting structure, not a rasteriser command. Windows keeps
 * it and reports it back, so remember the 24 bytes and do the same rather than
 * refusing the call (IGI aborts if SetClipStatus fails). */
static uint8_t g_clip_status[24];
static void dev_set_clip(void) {
    uint32_t p=A32(1);
    if(!p) RET(D3DERR_INVALIDCALL,2);
    memcpy(g_clip_status,hle_ptr(p),sizeof g_clip_status);
    RET(D3D_OK,2);
}
static void dev_get_clip(void) {
    uint32_t p=A32(1);
    if(!p) RET(D3DERR_INVALIDCALL,2);
    memcpy(hle_ptr(p),g_clip_status,sizeof g_clip_status);
    RET(D3D_OK,2);
}
static void dev_set_clip_plane(void) {    uint32_t index=A32(1),p=A32(2);
    float equation[4];
    if(!p) RET(E_POINTER,3);
    if(index>=g_max_clip_planes || index>=6) RET(D3DERR_INVALIDCALL,3);
    memcpy(equation,hle_ptr(p),sizeof equation);
    for(unsigned i=0;i<4;i++) if(!isfinite(equation[i])) RET(D3DERR_INVALIDCALL,3);
    if(!load_gl()) RET(D3DERR_NOTAVAILABLE,3);
    memcpy(g_clip_planes[index],equation,sizeof equation);
    g_clip_plane_valid[index]=1;
    refresh_clip_planes();
    RET(D3D_OK,3);
}
static void dev_get_clip_plane(void) {
    uint32_t index=A32(1),p=A32(2);
    if(!p) RET(E_POINTER,3);
    if(index>=g_max_clip_planes || index>=6) RET(D3DERR_INVALIDCALL,3);
    if(g_clip_plane_valid[index]) memcpy(hle_ptr(p),g_clip_planes[index],sizeof g_clip_planes[index]);
    else memset(hle_ptr(p),0,sizeof g_clip_planes[index]);
    RET(D3D_OK,3);
}
static void dev_set_material(void) {
    uint32_t p=A32(1); if(!p)RET(E_POINTER,2);
    if(!load_gl())RET(D3DERR_NOTAVAILABLE,2);
    memcpy(g_material,hle_ptr(p),sizeof g_material); apply_material(); RET(D3D_OK,2);
}
static void dev_get_material(void) {
    uint32_t p=A32(1); if(!p)RET(E_POINTER,2);
    memcpy(hle_ptr(p),g_material,sizeof g_material); RET(D3D_OK,2);
}
static void dev_set_light(void) {
    uint32_t index=A32(1),p=A32(2),type;
    if(!p)RET(E_POINTER,3);
    if(index>=g_max_lights||index>=8)RET(D3DERR_INVALIDCALL,3);
    memcpy(&type,hle_ptr(p),4);
    if(type<1||type>3)RET(D3DERR_NOTAVAILABLE,3);
    if(!load_gl())RET(D3DERR_NOTAVAILABLE,3);
    memcpy(g_lights[index],hle_ptr(p),sizeof g_lights[index]);
    g_light_valid[index]=1; apply_light(index); RET(D3D_OK,3);
}
static void dev_get_light(void) {
    uint32_t index=A32(1),p=A32(2);
    if(!p)RET(E_POINTER,3);
    if(index>=g_max_lights||index>=8||!g_light_valid[index])RET(D3DERR_INVALIDCALL,3);
    memcpy(hle_ptr(p),g_lights[index],sizeof g_lights[index]); RET(D3D_OK,3);
}
static void dev_light_enable(void) {
    uint32_t index=A32(1),enabled=A32(2);
    if(index>=g_max_lights||index>=8||!g_light_valid[index])RET(D3DERR_INVALIDCALL,3);
    g_light_enabled[index]=enabled!=0;
    if(!load_gl())RET(D3DERR_NOTAVAILABLE,3);
    if(g_light_enabled[index])p_enable(GL_LIGHT0+index);else p_disable(GL_LIGHT0+index);
    RET(D3D_OK,3);
}
static void dev_get_light_enable(void) {
    uint32_t index=A32(1),p=A32(2);
    if(!p)RET(E_POINTER,3);
    if(index>=g_max_lights||index>=8)RET(D3DERR_INVALIDCALL,3);
    MEM32(p)=g_light_enabled[index]?1u:0u; RET(D3D_OK,3);
}

/* The fixed-function GL color-material path can represent D3DMCS_COLOR1 for
 * diffuse and ambient, including their combined case. Other D3D material
 * sources have no equivalent in this immediate-mode bridge; reject them
 * instead of silently claiming to apply a state that has no effect. */
static void apply_color_material_state(void) {
    if (!load_gl()) return;
    /* D3DMCS_MATERIAL (0) and D3DMCS_COLOR1 (1) name the same material here:
     * there is no per-vertex diffuse/specular source, only the current
     * material. GL_COLOR_MATERIAL overrides the tracked one with glColor. */
    unsigned mode=0;
    if (g_render[145]==D3DMCS_COLOR1 && g_render[147]==D3DMCS_COLOR1) mode=GL_AMBIENT_AND_DIFFUSE;
    else if (g_render[145]==D3DMCS_COLOR1) mode=GL_DIFFUSE;
    else if (g_render[147]==D3DMCS_COLOR1) mode=GL_AMBIENT;
    if (g_render[141] && mode) {
        p_enable(GL_COLOR_MATERIAL);
        p_color_material(GL_FRONT_AND_BACK,mode);
    } else p_disable(GL_COLOR_MATERIAL);
}

static void apply_fog_state(void) {
    uint32_t mode=g_render[35]?g_render[35]:g_render[140];
    if(mode==1)p_fogi(GL_FOG_MODE,GL_EXP);
    else if(mode==2)p_fogi(GL_FOG_MODE,GL_EXP2);
    else if(mode==3)p_fogi(GL_FOG_MODE,GL_LINEAR);
    if(g_render[28] && mode)p_enable(GL_FOG);else p_disable(GL_FOG);
}

/* Direct3D 7 defines render states 1..153 with a few gaps. A state outside
 * that set is invalid on Windows; a defined state this backend cannot honour
 * is accepted and remembered, the way a Windows driver ignores an unsupported
 * capability instead of failing the call. */
static int render_state_defined(uint32_t s) {
    switch(s) {
    case 1: case 2: case 5:
    case 7: case 8: case 9: case 10:
    case 14: case 15: case 16:
    case 19: case 20:
    case 22: case 23: case 24: case 25: case 26: case 27: case 28: case 29:
    case 34: case 35: case 36: case 37: case 38:
    case 40: case 47: case 48:
    case 52: case 53: case 54: case 55: case 56: case 57: case 58: case 59: case 60:
    case 128: case 129: case 130: case 131: case 132: case 133: case 134: case 135:
    case 136: case 137: case 138: case 139: case 140: case 141: case 142: case 143:
    case 145: case 146: case 147: case 148:
    case 151: case 152: case 153:
        return 1;
    default:
        return s>=64 && s<=95;                 /* line stipple patterns */
    }
}
static int render_state_value_valid(uint32_t s,uint32_t v) {
    switch(s) {
    case 1: case 2: case 5: case 7: case 14: case 15: case 16: case 26: case 27:
    case 28: case 29: case 48: case 52: case 136: case 137: case 141:
    case 142: case 143: case 151: case 153:
        return v<=1;
    case 8: case 9: case 22:
        return v>=1 && v<=3;
    case 19: case 20:
        return v>=1 && v<=13;
    case 23: case 25:
        return v>=1 && v<=8;
    case 24: case 57:
        return v<=255;
    case 35: case 140:
        return v<=3;
    case 40: case 47:
        return v<=16;
    case 53: case 54: case 55: case 56:
        return v>=1 && v<=8;
    case 145: case 146: case 147: case 148:
        return v<=2;                          /* MATERIAL, COLOR1 or COLOR2 */
    case 152: {
        uint32_t mask=g_max_clip_planes?((1u<<g_max_clip_planes)-1u):0u;
        return (v&~mask)==0;
    }
    default:
        return 1;                             /* masks, colours, floats, patterns */
    }
}
static unsigned gl_stencil_op(uint32_t v) {
    switch(v) {
    case 2: return GL_ZERO;
    case 3: return GL_REPLACE;
    case 4: return GL_INCR;
    case 5: return GL_DECR;
    case 6: return GL_INVERT;
    case 7: return GL_INCR_WRAP;
    case 8: return GL_DECR_WRAP;
    default: return GL_KEEP;
    }
}
static void apply_stencil_state(void) {
    if(g_render[52]) p_enable(GL_STENCIL_TEST); else p_disable(GL_STENCIL_TEST);
    p_stencil_func(gl_cmp(g_render[56]?g_render[56]:8),(int)g_render[57],g_render[58]);
    p_stencil_mask(g_render[59]);
    p_stencil_op(gl_stencil_op(g_render[53]),gl_stencil_op(g_render[54]),gl_stencil_op(g_render[55]));
}
static void apply_blend(void) {
    uint32_t sf=g_render[19]?g_render[19]:2, df=g_render[20]?g_render[20]:1;
    unsigned fs,fd;
    if(sf==12||df==12) fs=fd=GL_SRC_ALPHA;                 /* D3DBLEND_BOTHSRCALPHA */
    else if(sf==13||df==13) fs=fd=GL_ONE_MINUS_SRC_ALPHA;  /* D3DBLEND_BOTHINVSRCALPHA */
    else fs=gl_blend(sf),fd=gl_blend(df);
    p_blend(fs,fd);
}
static void apply_clip_planes(void) {
    uint32_t mask=g_render[136]?g_render[152]:0u;
    for(unsigned i=0;i<g_max_clip_planes;i++) {
        if(mask&(1u<<i)) p_enable(GL_CLIP_PLANE0+i); else p_disable(GL_CLIP_PLANE0+i);
    }
}
static void dev_set_render_state(void) {
    uint32_t s=A32(1),v=A32(2);
    if(!render_state_defined(s)) { fprintf(stderr,"[d3d7] SetRenderState(%u,%u): undefined state\n",s,v); RET(D3DERR_INVALIDCALL,3); }
    if(!render_state_value_valid(s,v)) { fprintf(stderr,"[d3d7] SetRenderState(%u,%u): invalid value\n",s,v); RET(D3DERR_INVALIDCALL,3); }
    if(s==137 && v && getenv("RECOMP_D3D7_NOLIGHT")) v=0;
    if(!load_gl()) RET(D3DERR_NOTAVAILABLE,3);
    g_render[s]=v;
    switch(s) {
    case 7: if(v)p_enable(GL_DEPTH_TEST);else p_disable(GL_DEPTH_TEST);break;
    case 14: p_depth_mask(v?1:0);break;
    case 15: if(v)p_enable(GL_ALPHA_TEST);else p_disable(GL_ALPHA_TEST);break;
    case 26: if(v)p_enable(GL_DITHER);else p_disable(GL_DITHER);break;
    case 8: p_polygon(GL_FRONT_AND_BACK,v==1?GL_POINT:v==2?GL_LINE:GL_FILL);break;
    case 9: p_shade(v==1?GL_FLAT:GL_SMOOTH);break;
    case 19: case 20: apply_blend();break;
    case 22: if(v==1)p_disable(GL_CULL_FACE);else {p_enable(GL_CULL_FACE);p_cull(0x0404);p_front(v==2?0x0900:0x0901);}break;
    case 23: p_depth_func(gl_cmp(v));break;
    case 24: case 25: p_alpha(gl_cmp(g_render[25]?g_render[25]:8),(float)(g_render[24]&255)/255.f);break;
    case 27: if(v)p_enable(GL_BLEND);else p_disable(GL_BLEND);break;
    case 28: apply_fog_state();break;
    case 34: {float f[4]={(float)((v>>16)&255)/255.f,(float)((v>>8)&255)/255.f,(float)(v&255)/255.f,(float)(v>>24)/255.f};p_fogfv(0x0B66,f);break;}
    case 35: case 140: apply_fog_state();break;
    case 36: case 37: case 38: {float f;memcpy(&f,&v,4);p_fogf(s==36?0x0B63:s==37?0x0B64:0x0B62,f);break;}
    case 40: if(v)p_enable(GL_POLYGON_SMOOTH);else p_disable(GL_POLYGON_SMOOTH);break;
    case 47:
        if(v) { p_enable(GL_POLYGON_OFFSET_FILL); p_polygon_offset(0.f,-(float)(int)v); }
        else p_disable(GL_POLYGON_OFFSET_FILL);
        break;
    case 52: case 53: case 54: case 55: case 56: case 57: case 58: case 59: apply_stencil_state();break;
    case 60: apply_stage0_state();break;
    case 136: case 152: apply_clip_planes();break;
    case 137: if(v)p_enable(GL_LIGHTING);else p_disable(GL_LIGHTING);break;
    case 139: {float a[4]={(float)((v>>16)&255)/255.f,(float)((v>>8)&255)/255.f,(float)(v&255)/255.f,(float)(v>>24)/255.f};p_light_modelfv(GL_LIGHT_MODEL_AMBIENT,a);break;}
    case 141: case 145: case 147: apply_color_material_state();break;
    case 143: if(v)p_enable(GL_NORMALIZE);else p_disable(GL_NORMALIZE);break;
    case 29: apply_material();break;
    case 142: p_light_modeli(GL_LIGHT_MODEL_LOCAL_VIEWER,v?1:0);break;
    }
    RET(D3D_OK,3);
}
static void dev_get_render_state(void) {
    uint32_t s=A32(1),p=A32(2);
    if(!p) RET(E_POINTER,3);
    if(!render_state_defined(s)) RET(D3DERR_INVALIDCALL,3);
    MEM32(p)=g_render[s];
    RET(D3D_OK,3);
}
static void dev_draw_primitive(void) {
    uint32_t n=A32(4);
    RET(draw_vertices(A32(1),A32(2),A32(3),n,0,0)?D3D_OK:D3DERR_INVALIDCALL,6);
}
static void dev_draw_indexed_primitive(void) {
    uint32_t indices=A32(5),index_count=A32(6);
    if(!indices || !index_count) RET(D3DERR_INVALIDCALL,8);
    RET(draw_vertices(A32(1),A32(2),A32(3),A32(4),indices,index_count)?D3D_OK:D3DERR_INVALIDCALL,8);
}
static void dev_draw_vb(void) {
    uint32_t vb=A32(2),start=A32(3),count=A32(4);
    if(!vb || MEM32(vb)!=g_vb_vt) RET(D3DERR_INVALIDCALL,6);
    uint32_t fvf=MEM32(vb+16),stride=vertex_stride(fvf),capacity=stride?MEM32(vb+12)/stride:0;
    if(!stride || start>capacity || count>capacity-start) RET(D3DERR_INVALIDCALL,6);
    RET(draw_vertices(A32(1),fvf,MEM32(vb+8)+start*stride,count,0,0)?D3D_OK:D3DERR_INVALIDCALL,6);
}
static void dev_draw_indexed_vb(void) {
    uint32_t vb=A32(2),start=A32(3),count=A32(4),indices=A32(5),index_count=A32(6);
    if(!vb || MEM32(vb)!=g_vb_vt || !indices || !index_count) RET(D3DERR_INVALIDCALL,8);
    uint32_t fvf=MEM32(vb+16),stride=vertex_stride(fvf),capacity=stride?MEM32(vb+12)/stride:0;
    if(!stride || start>capacity || count>capacity-start) RET(D3DERR_INVALIDCALL,8);
    RET(draw_vertices(A32(1),fvf,MEM32(vb+8)+start*stride,count,indices,index_count)?D3D_OK:D3DERR_INVALIDCALL,8);
}

/* Device methods are added in the next section of this file. */
uint32_t hle_d3d7_create(void) {
    /* A brand-new object, or the existing one with a reference taken for the
     * caller. The create path in a title is QueryInterface(IID_IDirect3D7) on
     * IDirectDraw7, which uses hle_d3d7_get and takes its own reference. */
    uint32_t va = hle_d3d7_get();
    if (va) MEM32(va + 4)++;
    return va;
}
uint32_t hle_d3d7_get(void) {
    /* The object already exists: hand it back without asking for a screen
     * again. Headless, hle_screen_open leaves no GL context, so a second
     * request answers 0 even though the object is perfectly usable. */
    if (g_d3d) return hle_va(hle_ptr(g_d3d));
    if (!hle_screen_request_opengl()) return 0;
    if (!hle_screen_gl_active()) {
        const char *driver = getenv("SDL_VIDEODRIVER");
        int headless = driver && strcmp(driver, "dummy") == 0;
        if (hle_screen_open("Direct3D 7 application", 0, headless) != 0) return 0;
    }
    if(!g_d3d_vt) { static const win32hle_shim m[]={{"IDirect3D7::QueryInterface",d3d_qi},{"IDirect3D7::AddRef",d3d_addref},{"IDirect3D7::Release",d3d_release},{"IDirect3D7::EnumDevices",d3d_enum},{"IDirect3D7::CreateDevice",d3d_create_device},{"IDirect3D7::CreateVertexBuffer",d3d_create_vb},{"IDirect3D7::EnumZBufferFormats",d3d_enum_z},{"IDirect3D7::EvictManagedTextures",d3d_evict},{0,0}}; g_d3d_vt=hle_com_vtable(m); }
    /* g_d3d is the host pointer, as d3d_qi and d3d_release read it through
     * MEM32; the guest is handed its VA, as for every COM object here. */
    if(!g_d3d)g_d3d=hle_com_new(g_d3d_vt,8);
    return g_d3d ? hle_va(hle_ptr(g_d3d)) : 0;
}

/* The window presents the default framebuffer, but a device leaves its own
 * FBO bound between frames. The presenter must draw to the window and then
 * hand the target back: GL's framebuffer binding is not part of the attribute
 * stack the presenter saves, so it would otherwise clear the game's target. */
void hle_d3d7_present_begin(void) {
    if(!g_device || !load_gl() || !load_fbo()) return;
    p_bind_framebuffer(GL_FRAMEBUFFER,0);
}
void hle_d3d7_present_end(void) {
    if(!g_device || !load_fbo()) return;
    p_bind_framebuffer(GL_FRAMEBUFFER,
        hle_dd_surface_is_primary_3d_target(g_render_target) ? 0 : g_rt_framebuffer);
}
