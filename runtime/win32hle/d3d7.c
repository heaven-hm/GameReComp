/* A small Direct3D 7 fixed-function bridge over the screen's SDL OpenGL
 * context. The guest ABI remains 32-bit; guest structures and vertex buffers
 * are read through hle_ptr and never cast from guest addresses directly. */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
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
       GL_ALPHA_TEST=0x0BC0, GL_FOG=0x0B60, GL_LESS=0x0201,
       GL_LEQUAL=0x0203, GL_GREATER=0x0204, GL_GEQUAL=0x0206,
       GL_EQUAL=0x0202, GL_NOTEQUAL=0x0205, GL_ALWAYS=0x0207,
       GL_NEVER=0x0200, GL_ZERO=0, GL_ONE=1, GL_SRC_ALPHA=0x0302,
       GL_ONE_MINUS_SRC_ALPHA=0x0303, GL_DST_ALPHA=0x0304,
       GL_ONE_MINUS_DST_ALPHA=0x0305, GL_DST_COLOR=0x0306,
       GL_ONE_MINUS_DST_COLOR=0x0307, GL_SRC_COLOR=0x0300,
       GL_ONE_MINUS_SRC_COLOR=0x0301, GL_FRONT_AND_BACK=0x0408,
       GL_POINT=0x1B00, GL_LINE=0x1B01, GL_FILL=0x1B02,
       GL_FLAT=0x1D00, GL_SMOOTH=0x1D01 };
enum { GL_LIGHTING=0x0B50, GL_COLOR_MATERIAL=0x0B57, GL_LIGHT0=0x4000,
       GL_MAX_LIGHTS=0x0D31, GL_AMBIENT=0x1200, GL_DIFFUSE=0x1201,
       GL_SPECULAR=0x1202, GL_POSITION=0x1203, GL_SPOT_DIRECTION=0x1204,
       GL_SPOT_EXPONENT=0x1205, GL_SPOT_CUTOFF=0x1206,
       GL_CONSTANT_ATTENUATION=0x1207, GL_LINEAR_ATTENUATION=0x1208,
       GL_QUADRATIC_ATTENUATION=0x1209, GL_EMISSION=0x1600,
       GL_SHININESS=0x1601, GL_FRONT=0x0404, GL_LIGHT_MODEL_AMBIENT=0x0B53,
       GL_LIGHT_MODEL_LOCAL_VIEWER=0x0B51, GL_NORMALIZE=0x0BA1,
       GL_DEPTH_BITS=0x0D56, GL_STENCIL_BITS=0x0D57,
       GL_MAX_TEXTURE_SIZE=0x0D33,
       GL_AMBIENT_AND_DIFFUSE=0x1602 };
enum { GL_TEXTURE_2D=0x0DE1, GL_TEXTURE_ENV=0x2300, GL_TEXTURE_ENV_MODE=0x2200,
       GL_MODULATE=0x2100, GL_REPLACE=0x1E01, GL_RGB=0x1907, GL_RGBA=0x1908,
       GL_BGR=0x80E0, GL_BGRA=0x80E1, GL_UNSIGNED_BYTE=0x1401,
       GL_UNSIGNED_SHORT_5_6_5=0x8363, GL_TEXTURE_MIN_FILTER=0x2801,
       GL_TEXTURE_MAG_FILTER=0x2800, GL_TEXTURE_WRAP_S=0x2802,
       GL_TEXTURE_WRAP_T=0x2803, GL_NEAREST=0x2600, GL_LINEAR=0x2601,
       GL_REPEAT=0x2901, GL_CLAMP_TO_EDGE=0x812F, GL_UNPACK_ALIGNMENT=0x0CF5,
       GL_UNPACK_ROW_LENGTH=0x0CF2 };

typedef void (*gl_begin_fn)(unsigned); typedef void (*gl_end_fn)(void);
typedef void (*gl_vertex3_fn)(float,float,float); typedef void (*gl_vertex4_fn)(float,float,float,float);
typedef void (*gl_color4ub_fn)(unsigned char,unsigned char,unsigned char,unsigned char);
typedef void (*gl_normal3_fn)(float,float,float); typedef void (*gl_tex2_fn)(float,float);
typedef void (*gl_matrix_mode_fn)(unsigned); typedef void (*gl_load_matrix_fn)(const float *);
typedef void (*gl_mult_matrix_fn)(const float *);
typedef void (*gl_enable_fn)(unsigned); typedef void (*gl_disable_fn)(unsigned);
typedef void (*gl_clear_color_fn)(float,float,float,float); typedef void (*gl_clear_depth_fn)(double);
typedef void (*gl_clear_fn)(unsigned); typedef void (*gl_depth_func_fn)(unsigned);
typedef void (*gl_depth_mask_fn)(unsigned char); typedef void (*gl_blend_fn)(unsigned,unsigned);
typedef void (*gl_alpha_fn)(unsigned,float); typedef void (*gl_cull_fn)(unsigned);
typedef void (*gl_front_fn)(unsigned); typedef void (*gl_polygon_fn)(unsigned,unsigned);
typedef void (*gl_shade_fn)(unsigned); typedef void (*gl_fogfv_fn)(unsigned,const float *);
typedef void (*gl_fogi_fn)(unsigned,int); typedef void (*gl_fogf_fn)(unsigned,float);
typedef void (*gl_getint_fn)(unsigned,int *);
typedef void (*gl_bind_texture_fn)(unsigned,unsigned);
typedef void (*gl_gen_textures_fn)(int,unsigned *);
typedef void (*gl_tex_image_2d_fn)(unsigned,int,int,int,int,int,unsigned,unsigned,const void *);
typedef void (*gl_tex_parameter_i_fn)(unsigned,unsigned,int);
typedef void (*gl_pixel_store_i_fn)(unsigned,int);
typedef void (*gl_tex_envi_fn)(unsigned,unsigned,int);
typedef void (*gl_delete_textures_fn)(int,const unsigned *);
typedef void (*gl_materialfv_fn)(unsigned,unsigned,const float *);
typedef void (*gl_materialf_fn)(unsigned,unsigned,float);
typedef void (*gl_lightfv_fn)(unsigned,unsigned,const float *);
typedef void (*gl_lightf_fn)(unsigned,unsigned,float);
typedef void (*gl_color_material_fn)(unsigned,unsigned);
typedef void (*gl_light_modelfv_fn)(unsigned,const float *);
typedef void (*gl_light_modeli_fn)(unsigned,int);

static gl_begin_fn p_begin; static gl_end_fn p_end;
static gl_vertex3_fn p_vertex3; static gl_vertex4_fn p_vertex4;
static gl_color4ub_fn p_color4ub; static gl_normal3_fn p_normal3; static gl_tex2_fn p_tex2;
static gl_matrix_mode_fn p_matrix_mode; static gl_load_matrix_fn p_load_matrix;
static gl_mult_matrix_fn p_mult_matrix;
static gl_enable_fn p_enable; static gl_disable_fn p_disable;
static gl_clear_color_fn p_clear_color; static gl_clear_depth_fn p_clear_depth;
static gl_clear_fn p_clear; static gl_depth_func_fn p_depth_func;
static gl_depth_mask_fn p_depth_mask; static gl_blend_fn p_blend;
static gl_alpha_fn p_alpha; static gl_cull_fn p_cull; static gl_front_fn p_front;
static gl_polygon_fn p_polygon; static gl_shade_fn p_shade;
static gl_fogfv_fn p_fogfv; static gl_fogi_fn p_fogi; static gl_fogf_fn p_fogf;
static gl_getint_fn p_getint;
static gl_bind_texture_fn p_bind_texture;
static gl_gen_textures_fn p_gen_textures;
static gl_tex_image_2d_fn p_tex_image_2d;
static gl_tex_parameter_i_fn p_tex_parameter_i;
static gl_pixel_store_i_fn p_pixel_store_i;
static gl_tex_envi_fn p_tex_envi;
static gl_delete_textures_fn p_delete_textures;
static gl_materialfv_fn p_materialfv; static gl_materialf_fn p_materialf;
static gl_lightfv_fn p_lightfv; static gl_lightf_fn p_lightf;
static gl_color_material_fn p_color_material;
static gl_light_modelfv_fn p_light_modelfv;
static gl_light_modeli_fn p_light_modeli;
static int g_gl_loaded;

#define LOAD_GL(field, type, symbol) do { *(void **)(&(field)) = SDL_GL_GetProcAddress(symbol); if (!(field)) return 0; } while (0)
static int load_gl(void) {
    if (g_gl_loaded) return 1;
    if (!hle_screen_gl_active()) return 0;
    LOAD_GL(p_begin, gl_begin_fn, "glBegin"); LOAD_GL(p_end, gl_end_fn, "glEnd");
    LOAD_GL(p_vertex3, gl_vertex3_fn, "glVertex3f"); LOAD_GL(p_vertex4, gl_vertex4_fn, "glVertex4f");
    LOAD_GL(p_color4ub, gl_color4ub_fn, "glColor4ub"); LOAD_GL(p_normal3, gl_normal3_fn, "glNormal3f");
    LOAD_GL(p_tex2, gl_tex2_fn, "glTexCoord2f"); LOAD_GL(p_matrix_mode, gl_matrix_mode_fn, "glMatrixMode");
    LOAD_GL(p_load_matrix, gl_load_matrix_fn, "glLoadMatrixf"); LOAD_GL(p_mult_matrix, gl_mult_matrix_fn, "glMultMatrixf");
    LOAD_GL(p_enable, gl_enable_fn, "glEnable");
    LOAD_GL(p_disable, gl_disable_fn, "glDisable"); LOAD_GL(p_clear_color, gl_clear_color_fn, "glClearColor");
    LOAD_GL(p_clear_depth, gl_clear_depth_fn, "glClearDepth"); LOAD_GL(p_clear, gl_clear_fn, "glClear");
    LOAD_GL(p_depth_func, gl_depth_func_fn, "glDepthFunc"); LOAD_GL(p_depth_mask, gl_depth_mask_fn, "glDepthMask");
    LOAD_GL(p_blend, gl_blend_fn, "glBlendFunc"); LOAD_GL(p_alpha, gl_alpha_fn, "glAlphaFunc");
    LOAD_GL(p_cull, gl_cull_fn, "glCullFace"); LOAD_GL(p_front, gl_front_fn, "glFrontFace");
    LOAD_GL(p_polygon, gl_polygon_fn, "glPolygonMode"); LOAD_GL(p_shade, gl_shade_fn, "glShadeModel");
    LOAD_GL(p_fogfv, gl_fogfv_fn, "glFogfv"); LOAD_GL(p_fogi, gl_fogi_fn, "glFogi");
    LOAD_GL(p_fogf, gl_fogf_fn, "glFogf"); LOAD_GL(p_getint, gl_getint_fn, "glGetIntegerv");
    LOAD_GL(p_bind_texture, gl_bind_texture_fn, "glBindTexture");
    LOAD_GL(p_gen_textures, gl_gen_textures_fn, "glGenTextures");
    LOAD_GL(p_tex_image_2d, gl_tex_image_2d_fn, "glTexImage2D");
    LOAD_GL(p_tex_parameter_i, gl_tex_parameter_i_fn, "glTexParameteri");
    LOAD_GL(p_pixel_store_i, gl_pixel_store_i_fn, "glPixelStorei");
    LOAD_GL(p_tex_envi, gl_tex_envi_fn, "glTexEnvi");
    LOAD_GL(p_delete_textures, gl_delete_textures_fn, "glDeleteTextures");
    LOAD_GL(p_materialfv, gl_materialfv_fn, "glMaterialfv");
    LOAD_GL(p_materialf, gl_materialf_fn, "glMaterialf");
    LOAD_GL(p_lightfv, gl_lightfv_fn, "glLightfv");
    LOAD_GL(p_lightf, gl_lightf_fn, "glLightf");
    LOAD_GL(p_color_material, gl_color_material_fn, "glColorMaterial");
    LOAD_GL(p_light_modelfv, gl_light_modelfv_fn, "glLightModelfv");
    LOAD_GL(p_light_modeli, gl_light_modeli_fn, "glLightModeli");
    g_gl_loaded = 1;
    return 1;
}
#undef LOAD_GL

static const uint8_t IID_IUNKNOWN[16] = {0,0,0,0,0,0,0,0,0xC0,0,0,0,0,0,0,0x46};
static const uint8_t IID_IDIRECT3D7[16] = {0x78,0x9E,0x04,0xF5,0x61,0x48,0xD2,0x11,0xA4,0x07,0,0xA0,0xC9,0x06,0x29,0xA8};
static uint32_t g_d3d_vt, g_d3d;
static uint32_t g_dev_vt;
static uint32_t g_vb_vt;
static uint32_t g_device;
static uint32_t g_render_target;
static float g_world[16], g_view[16], g_projection[16];
static uint32_t g_render[256];
static uint32_t g_viewport[6];
static uint32_t g_texture_surface;
static unsigned g_gl_texture;
static uint32_t g_stage_state[32];
static uint8_t g_material[68];
static uint8_t g_lights[8][104];
static uint8_t g_light_valid[8], g_light_enabled[8];
static unsigned g_max_lights;

#define D3D7_DEVICE_DESC_SIZE 236u
#define D3D7_PRIM_CAPS_SIZE 56u
/* Direct3D 7 clients pass D3DDEVICEDESC7.deviceGUID back to CreateDevice.
 * Use the standard RGB device identity for this software-compatible fixed-
 * function backend; the private OpenGL label remains in the description.
 * This avoids claiming the HAL or hardware transform-and-light capabilities. */
static const uint8_t g_d3d7_gl_device_guid[16] = {
    0x60,0x5C,0x66,0xA4, 0x73,0x26, 0xCF,0x11,
    0xA3,0x1A,0x00,0xAA,0x00,0xB9,0x33,0x56
};

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
static void dev_draw_vb(void);
static void dev_draw_indexed_vb(void);
static void d3d_create_vb(void);
static void dev_get_texture(void);
static void dev_set_texture(void);
static void dev_get_tss(void);
static void dev_set_tss(void);
static void refresh_lights(void);
static void apply_material(void);
static void apply_color_material_state(void);
static void dev_set_material(void);
static void dev_get_material(void);
static void dev_set_light(void);
static void dev_get_light(void);
static void dev_light_enable(void);
static void dev_get_light_enable(void);
static void dev_get_caps(void);
static void dev_enum_textures(void);
static void d3d_enum_z(void);
static void d3d_evict(void);
static void vb_lock(void);
static void vb_unlock(void);
static void vb_get_desc(void);
static void release_surface_ref(uint32_t surface);

#define UNIMPL(name, argc) static void name(void) { RET(D3DERR_NOTAVAILABLE, argc); }
UNIMPL(dev_begin_state,1)
UNIMPL(dev_end_state,2) UNIMPL(dev_preload,2) UNIMPL(dev_set_clip,2)
UNIMPL(dev_get_clip,2) UNIMPL(dev_draw_strided,6) UNIMPL(dev_draw_indexed_strided,8)
UNIMPL(dev_sphere,6) UNIMPL(dev_validate,2)
UNIMPL(dev_apply_state,2) UNIMPL(dev_capture_state,2) UNIMPL(dev_delete_state,2)
UNIMPL(dev_create_state,3) UNIMPL(dev_load,6) UNIMPL(dev_set_clip_plane,3) UNIMPL(dev_get_clip_plane,3)
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
static unsigned gl_cmp(uint32_t v) {
    static const unsigned t[]={GL_NEVER,GL_NEVER,GL_LESS,GL_EQUAL,GL_LEQUAL,GL_GREATER,GL_NOTEQUAL,GL_GEQUAL,GL_ALWAYS};
    return v<sizeof(t)/sizeof(t[0])?t[v]:GL_LEQUAL;
}
static unsigned gl_blend(uint32_t v) {
    static const unsigned t[]={GL_ZERO,GL_ZERO,GL_ONE,GL_SRC_COLOR,GL_ONE_MINUS_SRC_COLOR,GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA,GL_DST_ALPHA,GL_ONE_MINUS_DST_ALPHA,GL_DST_COLOR,GL_ONE_MINUS_DST_COLOR};
    if (v==11) return GL_SRC_ALPHA;
    return v<sizeof(t)/sizeof(t[0])?t[v]:GL_ONE;
}
static uint32_t vertex_stride(uint32_t fvf) {
    uint32_t p=fvf&0x00e, n=(p==4?4:3), s=n*4;
    if (p!=2 && p!=4) return 0;
    if (fvf&0x010) s+=12;
    if (fvf&0x040) s+=4;
    if (fvf&0x080) s+=4;
    uint32_t tc=(fvf>>8)&15;
    for (uint32_t i=0;i<tc;i++) {
        uint32_t dimcode=(fvf>>(16+i*2))&3;
        s+=4*(dimcode==1?1:dimcode==2?3:dimcode==3?4:2);
    }
    return s;
}
static unsigned gl_primitive(uint32_t p) {
    switch(p) { case 1:return GL_POINTS; case 2:return GL_LINES; case 3:return GL_LINE_STRIP;
    case 4:return GL_TRIANGLES; case 5:return GL_TRIANGLE_STRIP; case 6:return GL_TRIANGLE_FAN; default:return 0xffffffffu; }
}
static void apply_stage0_state(void);

static void emit_vertex(const uint8_t *v, uint32_t fvf, int screen_xy) {
    uint32_t pos=fvf&0x00e, off=(pos==4?16:12);
    float x,y,z;
    memcpy(&x,v,4); memcpy(&y,v+4,4); memcpy(&z,v+8,4);
    if(fvf&0x010) { float n[3]; memcpy(n,v+off,12); p_normal3(n[0],n[1],n[2]); off+=12; }
    if(fvf&0x040) { uint32_t c; memcpy(&c,v+off,4); p_color4ub((uint8_t)(c>>16),(uint8_t)(c>>8),(uint8_t)c,(uint8_t)(c>>24)); off+=4; }
    if(fvf&0x080) off+=4;
    if(((fvf>>8)&15)>0) { float uv[2]={0,0}; memcpy(uv,v+off,8); p_tex2(uv[0],uv[1]); }
    if(screen_xy) {
        int vp[4]={0,0,640,480};
        p_getint(0x0BA2,(int *)vp);
        float w=(float)(vp[2]>0?vp[2]:640), h=(float)(vp[3]>0?vp[3]:480);
        p_vertex4(2.f*x/w-1.f, 1.f-2.f*y/h, z, 1.f);
    } else p_vertex3(x,y,z);
}
static int draw_vertices(uint32_t primitive, uint32_t fvf, uint32_t vertices,
                         uint32_t vertex_count, uint32_t indices, uint32_t index_count) {
    unsigned mode=gl_primitive(primitive); uint32_t stride=vertex_stride(fvf);
    if(!load_gl() || mode==0xffffffffu || !stride || !vertices) return 0;
    if(vertex_count>1000000u || index_count>3000000u) return 0;
    if(g_texture_surface && g_stage_state[1]!=1 && ((fvf >> 8) & 15)) {
        uint8_t *pixels; int w,h,pitch,bpp;
        if(!hle_dd_surface_pixels(g_texture_surface,&pixels,&w,&h,&pitch,&bpp) || w<=0 || h<=0) return 0;
        unsigned format, type;
        if(bpp==16) format=GL_RGB,type=GL_UNSIGNED_SHORT_5_6_5;
        else if(bpp==24) format=GL_BGR,type=GL_UNSIGNED_BYTE;
        else if(bpp==32) format=GL_BGRA,type=GL_UNSIGNED_BYTE;
        else return 0;
        if(!g_gl_texture) p_gen_textures(1,&g_gl_texture);
        p_enable(GL_TEXTURE_2D); p_bind_texture(GL_TEXTURE_2D,g_gl_texture);
        p_tex_parameter_i(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);
        p_tex_parameter_i(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
        p_tex_parameter_i(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_REPEAT);
        p_tex_parameter_i(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_REPEAT);
        p_pixel_store_i(GL_UNPACK_ALIGNMENT,1); p_pixel_store_i(GL_UNPACK_ROW_LENGTH,pitch/(bpp/8));
        p_tex_image_2d(GL_TEXTURE_2D,0,bpp==32?GL_RGBA:GL_RGB,w,h,0,format,type,pixels);
        p_pixel_store_i(GL_UNPACK_ROW_LENGTH,0); p_pixel_store_i(GL_UNPACK_ALIGNMENT,4);
        apply_stage0_state();
    } else p_disable(GL_TEXTURE_2D);
    const uint8_t *base=hle_ptr(vertices); const uint16_t *idx=indices?(const uint16_t *)hle_ptr(indices):NULL;
    p_begin(mode);
    if(idx) for(uint32_t i=0;i<index_count;i++) { if(idx[i]>=vertex_count){p_end();return 0;} emit_vertex(base+(uint32_t)idx[i]*stride,fvf,(fvf&0x00e)==4); }
    else for(uint32_t i=0;i<vertex_count;i++) emit_vertex(base+i*stride,fvf,(fvf&0x00e)==4);
    p_end();
    return 1;
}

static void d3d_qi(void) { uint32_t s=A32(0),iid=A32(1),out=A32(2); if(!iid||!out) RET(E_POINTER,3);
    const uint8_t *g=hle_ptr(iid); if(!memcmp(g,IID_IUNKNOWN,16)||!memcmp(g,IID_IDIRECT3D7,16)) { MEM32(out)=s; MEM32(s+4)++; RET(D3D_OK,3); }
    MEM32(out)=0; RET(E_NOINTERFACE,3); }
static void d3d_addref(void) { RET(++MEM32(A32(0)+4),1); }
static void d3d_release(void) { uint32_t s=A32(0),r=MEM32(s+4); if(r)r--; MEM32(s+4)=r; if(!r){hle_com_free(s);if(g_d3d==s)g_d3d=0;} RET(r,1); }
static void dev_qi(void) { uint32_t s=A32(0),out=A32(2); if(!out) RET(E_POINTER,3); MEM32(out)=s; MEM32(s+4)++; RET(D3D_OK,3); }
static void dev_addref(void) { RET(++MEM32(A32(0)+4),1); }
static void dev_release(void) { uint32_t s=A32(0),r=MEM32(s+4); if(r)r--; MEM32(s+4)=r; if(!r){if(MEM32(s+8))hle_free(hle_ptr(MEM32(s+8)));hle_com_free(s);if(g_device==s){g_device=0;release_surface_ref(g_texture_surface);g_texture_surface=0;release_surface_ref(g_render_target);g_render_target=0;if(g_gl_texture&&load_gl())p_delete_textures(1,&g_gl_texture);g_gl_texture=0;}} RET(r,1); }
static void vb_qi(void) { uint32_t s=A32(0),out=A32(2); if(!out) RET(E_POINTER,3); MEM32(out)=s; MEM32(s+4)++; RET(D3D_OK,3); }
static void vb_addref(void) { RET(++MEM32(A32(0)+4),1); }
static void vb_release(void) { uint32_t s=A32(0),r=MEM32(s+4); if(r)r--; MEM32(s+4)=r; if(!r){if(MEM32(s+8))hle_free(hle_ptr(MEM32(s+8)));hle_com_free(s);} RET(r,1); }

static void d3d_write_prim_caps(uint32_t p, int depth_bits) {
    MEM32(p)=D3D7_PRIM_CAPS_SIZE;
    MEM32(p+4)=0x70u;                          /* cull none, CW and CCW */
    MEM32(p+8)=depth_bits?0x30u:0x20u;        /* z test when the GL context has depth */
    MEM32(p+12)=depth_bits?0xFFu:0u;          /* all D3D compare functions */
    MEM32(p+16)=MEM32(p+20)=0x3FFu;           /* blend factors mapped by gl_blend */
    MEM32(p+24)=0xFFu;                         /* alpha compare functions */
    MEM32(p+28)=0xAu;                           /* flat and Gouraud RGB shading */
    MEM32(p+32)=0xDu;                           /* perspective, alpha, transparency */
    MEM32(p+36)=0x03000303u;                   /* point/linear min and mag filters */
    MEM32(p+40)=0x42u;                          /* texture select/copy and modulate */
    MEM32(p+44)=0x5u;                           /* wrap and clamp */
    MEM32(p+48)=MEM32(p+52)=0;
}

static void d3d_write_device_desc7(uint32_t p) {
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

    MEM32(p+0)=0x540u;                         /* system-memory TL/texture, DrawPrimitive TL */
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
    memcpy(hle_ptr(p+196),g_d3d7_gl_device_guid,sizeof g_d3d7_gl_device_guid);
    MEM16(p+212)=0; MEM16(p+214)=0;            /* user clip planes and blend matrices */
    MEM32(p+216)=0x38u;                        /* directional/positional lights, local viewer */
}

static void d3d_enum(void) {
    uint32_t cb=A32(1),ctx=A32(2),desc;
    static uint32_t description,name;
    if(!cb)RET(E_POINTER,3);
    if(!hle_screen_gl_active()||!load_gl())RET(D3DERR_NOTAVAILABLE,3);
    if(!description)description=hle_guest_strdup("SDL OpenGL fixed-function Direct3D 7 (RGB-compatible)");
    if(!name)name=hle_guest_strdup("SDL OpenGL");
    desc=hle_va(hle_alloc(D3D7_DEVICE_DESC_SIZE));
    if(!desc)RET(D3DERR_NOTAVAILABLE,3);
    d3d_write_device_desc7(desc);
    uint32_t args[4]={description,name,desc,ctx};
    hle_call_guest(cb,4,args);
    hle_free(hle_ptr(desc));
    RET(D3D_OK,3);
}

static void d3d_enum_z(void) {
    uint32_t iid=A32(1),cb=A32(2),ctx=A32(3);
    int depth_bits=0;
    if(!iid||!cb)RET(E_POINTER,4);
    if(memcmp(hle_ptr(iid),g_d3d7_gl_device_guid,16))RET(D3DERR_NOTAVAILABLE,4);
    if(!hle_screen_gl_active()||!load_gl())RET(D3DERR_NOTAVAILABLE,4);
    p_getint(GL_DEPTH_BITS,&depth_bits);
    if(depth_bits<16)RET(D3DERR_NOTAVAILABLE,4);
    uint32_t pf=hle_va(hle_alloc(32));
    if(!pf)RET(D3DERR_NOTAVAILABLE,4);
    memset(hle_ptr(pf),0,32);
    MEM32(pf)=32; MEM32(pf+4)=0x400u;       /* DDPF_ZBUFFER */
    MEM32(pf+12)=(uint32_t)depth_bits;
    MEM32(pf+20)=depth_bits>=32?0xFFFFFFFFu:depth_bits>=24?0x00FFFFFFu:0x0000FFFFu;
    uint32_t args[2]={pf,ctx};
    hle_call_guest(cb,2,args);
    hle_free(hle_ptr(pf));
    RET(D3D_OK,4);
}

static void dev_get_caps(void) {
    uint32_t p=A32(1);
    if(!p)RET(E_POINTER,2);
    if(!hle_screen_gl_active()||!load_gl())RET(D3DERR_NOTAVAILABLE,2);
    d3d_write_device_desc7(p);
    RET(D3D_OK,2);
}

static void dev_enum_textures(void) {
    uint32_t cb=A32(1),ctx=A32(2);
    if(!cb)RET(E_POINTER,3);
    if(!hle_screen_gl_active()||!load_gl())RET(D3DERR_NOTAVAILABLE,3);
    uint32_t pf=hle_va(hle_alloc(32));
    if(!pf)RET(D3DERR_NOTAVAILABLE,3);
    static const uint32_t formats[3][8]={
        {32,0x40,0,16,0xF800,0x07E0,0x001F,0},
        {32,0x40,0,24,0xFF0000,0x00FF00,0x0000FF,0},
        {32,0x41,0,32,0x00FF0000,0x0000FF00,0x000000FF,0xFF000000}
    };
    for(unsigned i=0;i<3;i++) {
        memcpy(hle_ptr(pf),formats[i],sizeof formats[i]);
        uint32_t args[2]={pf,ctx};
        if(!hle_call_guest(cb,2,args))break;
    }
    hle_free(hle_ptr(pf));
    RET(D3D_OK,3);
}

static void d3d_evict(void) { RET(D3D_OK,1); }
static void d3d_create_device(void) {
    uint32_t guid=A32(1),target=A32(2),out=A32(3);
    if(!out)RET(E_POINTER,4); MEM32(out)=0;
    if(!guid)RET(E_POINTER,4);
    if(memcmp(hle_ptr(guid),g_d3d7_gl_device_guid,16))RET(D3DERR_NOTAVAILABLE,4);
    if(!hle_dd_surface_is_3d_target(target))RET(D3DERR_INVALIDCALL,4);
    if(!hle_dd_surface_is_primary_3d_target(target))RET(D3DERR_NOTAVAILABLE,4);
    if(!hle_screen_gl_active()||!load_gl())RET(D3DERR_NOTAVAILABLE,4);
    if(g_device && g_render_target!=target)RET(D3DERR_NOTAVAILABLE,4);
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
        identity(g_world); identity(g_view); identity(g_projection);
        memset(g_render,0,sizeof g_render); g_render[7]=1; g_render[8]=3; g_render[9]=2;
        g_render[137]=1; g_render[141]=1; g_render[145]=1; g_render[147]=0; g_render[148]=0;
        memset(g_material,0,sizeof g_material); memset(g_lights,0,sizeof g_lights);
        memset(g_light_valid,0,sizeof g_light_valid); memset(g_light_enabled,0,sizeof g_light_enabled);
        int max_lights=0; p_getint(GL_MAX_LIGHTS,&max_lights);
        g_max_lights=max_lights<0?0u:max_lights>8?8u:(unsigned)max_lights;
        memset(g_stage_state,0,sizeof g_stage_state); g_stage_state[1]=4; g_stage_state[2]=2; g_stage_state[3]=1;
        g_stage_state[5]=2; g_stage_state[6]=2; g_stage_state[13]=1; g_stage_state[14]=1;
        g_viewport[2]=640;g_viewport[3]=480;g_viewport[4]=0;g_viewport[5]=0x3f800000u;
        float ambient[4]={0,0,0,1}; p_light_modelfv(GL_LIGHT_MODEL_AMBIENT,ambient);
        p_light_modeli(GL_LIGHT_MODEL_LOCAL_VIEWER,0); p_enable(GL_LIGHTING);
        apply_color_material_state();
        apply_material();
        g_render_target=target; MEM32(target+4)++;
    } else MEM32(g_device+4)++;
    MEM32(out)=g_device; RET(D3D_OK,4);
}

static void d3d_create_vb(void) {
    uint32_t desc=A32(1),out=A32(2); if(!out)RET(E_POINTER,4); MEM32(out)=0;
    if(!desc)RET(E_POINTER,4);
    uint32_t fvf=MEM32(desc+8), count=MEM32(desc+12), stride=vertex_stride(fvf);
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
    if(flags || !hle_dd_surface_is_3d_target(surface)) RET(D3DERR_INVALIDCALL,3);
    if(!hle_dd_surface_is_primary_3d_target(surface)) RET(D3DERR_NOTAVAILABLE,3);
    if(!hle_screen_gl_active()) RET(D3DERR_NOTAVAILABLE,3);
    if(surface==g_render_target) RET(D3D_OK,3);
    MEM32(surface+4)++;
    uint32_t old=g_render_target;
    g_render_target=surface;
    release_surface_ref(old);
    RET(D3D_OK,3);
}
static void dev_get_target(void) {
    uint32_t out=A32(1);
    if(!out) RET(E_POINTER,2);
    MEM32(out)=g_render_target;
    if(g_render_target) MEM32(g_render_target+4)++;
    RET(D3D_OK,2);
}
static int stage0_color_mode(void) {
    uint32_t op=g_stage_state[1];
    if (op==2 && g_stage_state[2]==2) return GL_REPLACE; /* select texture arg */
    if (op==3 && g_stage_state[3]==2) return GL_REPLACE;
    return GL_MODULATE;
}
static int gl_filter(uint32_t value) { return value==1?GL_NEAREST:GL_LINEAR; }
static int gl_address(uint32_t value) { return value==3?GL_CLAMP_TO_EDGE:GL_REPEAT; }
static void apply_stage0_state(void) {
    if (!load_gl() || !g_gl_texture) return;
    p_bind_texture(GL_TEXTURE_2D,g_gl_texture);
    p_tex_parameter_i(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,gl_filter(g_stage_state[6]));
    p_tex_parameter_i(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,gl_filter(g_stage_state[5]));
    p_tex_parameter_i(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,gl_address(g_stage_state[13]));
    p_tex_parameter_i(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,gl_address(g_stage_state[14]));
    p_tex_envi(GL_TEXTURE_ENV,GL_TEXTURE_ENV_MODE,stage0_color_mode());
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
    if (stage) RET(D3DERR_NOTAVAILABLE,3);
    if (surface) {
        uint8_t *pixels; int w,h,pitch,bpp;
        if (!hle_dd_surface_pixels(surface,&pixels,&w,&h,&pitch,&bpp)) RET(D3DERR_INVALIDCALL,3);
        (void)pixels; (void)w; (void)h; (void)pitch; (void)bpp;
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
    if (stage || state>=32) RET(D3DERR_NOTAVAILABLE,4);
    MEM32(out)=g_stage_state[state]; RET(D3D_OK,4);
}
static void dev_set_tss(void) {
    uint32_t stage=A32(1),state=A32(2),value=A32(3);
    if (stage || state>=32 || (state!=1 && state!=2 && state!=3 && state!=5 && state!=6 && state!=7 && state!=13 && state!=14))
        RET(D3DERR_NOTAVAILABLE,4);
    if ((state==1 && value>4) || ((state==5 || state==6 || state==7) && value>2) ||
        ((state==13 || state==14) && (value<1 || value>5))) RET(D3DERR_INVALIDCALL,4);
    g_stage_state[state]=value;
    if (state==1 || state==2 || state==3 || state==5 || state==6 || state==13 || state==14) apply_stage0_state();
    RET(D3D_OK,4);
}
static void dev_get_direct3d(void) { uint32_t out=A32(1);if(!out)RET(E_POINTER,2);MEM32(out)=g_d3d;MEM32(g_d3d+4)++;RET(D3D_OK,2); }
static void dev_begin_scene(void) { RET(load_gl()?D3D_OK:D3DERR_NOTAVAILABLE,1); }
static void dev_end_scene(void) { hle_screen_gl_swap(); RET(D3D_OK,1); }
static void dev_clear(void) { uint32_t flags=A32(3),c=A32(4),zb=A32(5); float z;memcpy(&z,&zb,4); if(!load_gl())RET(D3DERR_NOTAVAILABLE,7);
    if(flags&1){p_clear_color((float)((c>>16)&255)/255.f,(float)((c>>8)&255)/255.f,(float)(c&255)/255.f,(float)(c>>24)/255.f);}
    if(flags&2)p_clear_depth((double)z); unsigned m=0;if(flags&1)m|=0x4000;if(flags&2)m|=0x0100;if(flags&4)m|=0x0400;p_clear(m);RET(D3D_OK,7); }

static float *transform_for(uint32_t state) { if(state==1)return g_world;if(state==2)return g_view;if(state==3)return g_projection;return NULL; }
static void dev_set_transform(void) { uint32_t st=A32(1),m=A32(2);float *to=transform_for(st);if(!to||!m)RET(D3DERR_INVALIDCALL,3);memcpy(to,hle_ptr(m),64);if(st==1||st==2){refresh_modelview();if(st==2)refresh_lights();}if(st==3)load_projection();RET(D3D_OK,3); }
static void dev_get_transform(void) { uint32_t st=A32(1),m=A32(2);float *from=transform_for(st);if(!from||!m)RET(D3DERR_INVALIDCALL,3);memcpy(hle_ptr(m),from,64);RET(D3D_OK,3); }
static void dev_multiply_transform(void) { uint32_t st=A32(1),m=A32(2);float *to=transform_for(st);if(!to||!m)RET(D3DERR_INVALIDCALL,3);float a[16],b[16],r[16];memcpy(a,to,64);memcpy(b,hle_ptr(m),64);for(int i=0;i<4;i++)for(int j=0;j<4;j++){r[i*4+j]=0;for(int k=0;k<4;k++)r[i*4+j]+=a[i*4+k]*b[k*4+j];}memcpy(to,r,64);if(st==1||st==2){refresh_modelview();if(st==2)refresh_lights();}if(st==3)load_projection();RET(D3D_OK,3); }
static void dev_set_viewport(void) { uint32_t p=A32(1);if(!p)RET(E_POINTER,2);for(int i=0;i<6;i++)g_viewport[i]=MEM32(p+i*4);if(load_gl())hle_screen_gl_viewport((int)g_viewport[0],(int)g_viewport[1],(int)g_viewport[2],(int)g_viewport[3]);RET(D3D_OK,2); }
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
    unsigned mode=0;
    if (g_render[145]==1 && g_render[147]==1) mode=GL_AMBIENT_AND_DIFFUSE;
    else if (g_render[145]==1) mode=GL_DIFFUSE;
    else if (g_render[147]==1) mode=GL_AMBIENT;
    if (g_render[141] && mode) {
        p_enable(GL_COLOR_MATERIAL);
        p_color_material(GL_FRONT_AND_BACK,mode);
    } else p_disable(GL_COLOR_MATERIAL);
}

static void dev_set_render_state(void) { uint32_t s=A32(1),v=A32(2);if((s==145||s==147)&&v>1)RET(D3DERR_NOTAVAILABLE,3);if((s==146||s==148)&&v!=0)RET(D3DERR_NOTAVAILABLE,3);if(!load_gl())RET(D3DERR_NOTAVAILABLE,3);if(s<256)g_render[s]=v;
    switch(s){case 7:if(v)p_enable(GL_DEPTH_TEST);else p_disable(GL_DEPTH_TEST);break;
    case 8:p_polygon(GL_FRONT_AND_BACK,v==1?GL_POINT:v==2?GL_LINE:GL_FILL);break;
    case 9:p_shade(v==1?GL_FLAT:GL_SMOOTH);break;
    case 19:case 20:p_blend(gl_blend(g_render[19]?g_render[19]:2),gl_blend(g_render[20]?g_render[20]:1));break;
    case 22:if(v==1)p_disable(GL_CULL_FACE);else {p_enable(GL_CULL_FACE);p_cull(0x0404);p_front(v==2?0x0900:0x0901);}break;
    case 23:p_depth_func(gl_cmp(v));break;
    case 24:case 25:p_alpha(gl_cmp(g_render[25]?g_render[25]:8),(float)(g_render[24]&255)/255.f);break;
    case 27:if(v)p_enable(GL_BLEND);else p_disable(GL_BLEND);break;
    case 28:if(v)p_enable(GL_FOG);else p_disable(GL_FOG);break;
    case 34:{float f[4]={(float)((v>>16)&255)/255.f,(float)((v>>8)&255)/255.f,(float)(v&255)/255.f,(float)(v>>24)/255.f};p_fogfv(0x0B66,f);break;}
    case 36:case 37:case 38:{float f;memcpy(&f,&v,4);p_fogf(s==36?0x0B63:s==37?0x0B64:0x0B62,f);break;}
    case 137:if(v)p_enable(GL_LIGHTING);else p_disable(GL_LIGHTING);break;
    case 139:{float a[4]={(float)((v>>16)&255)/255.f,(float)((v>>8)&255)/255.f,(float)(v&255)/255.f,(float)(v>>24)/255.f};p_light_modelfv(GL_LIGHT_MODEL_AMBIENT,a);break;}
    case 141:apply_color_material_state();break;
    case 143:if(v)p_enable(GL_NORMALIZE);else p_disable(GL_NORMALIZE);break;
    case 29:apply_material();break;
    case 142:p_light_modeli(GL_LIGHT_MODEL_LOCAL_VIEWER,v?1:0);break;
    case 145:case 147:
        apply_color_material_state();break;
    case 146:case 148:
        break;
    default:break;} RET(D3D_OK,3); }
static void dev_get_render_state(void) { uint32_t s=A32(1),p=A32(2);if(!p)RET(E_POINTER,3);MEM32(p)=s<256?g_render[s]:0;RET(D3D_OK,3); }
static void dev_draw_primitive(void) { uint32_t n=A32(4);RET(draw_vertices(A32(1),A32(2),A32(3),n,0,0)?D3D_OK:D3DERR_INVALIDCALL,6); }
static void dev_draw_indexed_primitive(void) { RET(draw_vertices(A32(1),A32(2),A32(3),A32(4),A32(5),A32(6))?D3D_OK:D3DERR_INVALIDCALL,8); }
static void dev_draw_vb(void) { uint32_t vb=A32(2),start=A32(3),count=A32(4),stride=vertex_stride(MEM32(vb+16));RET(draw_vertices(A32(1),MEM32(vb+16),MEM32(vb+8)+start*stride,count,0,0)?D3D_OK:D3DERR_INVALIDCALL,6); }
static void dev_draw_indexed_vb(void) { uint32_t vb=A32(2),start=A32(3),count=A32(4),stride=vertex_stride(MEM32(vb+16));RET(draw_vertices(A32(1),MEM32(vb+16),MEM32(vb+8)+start*stride,count,A32(5),A32(6))?D3D_OK:D3DERR_INVALIDCALL,8); }

/* Device methods are added in the next section of this file. */
uint32_t hle_d3d7_create(void) {
    if (!hle_screen_request_opengl()) return 0;
    if (!hle_screen_gl_active()) {
        const char *driver = getenv("SDL_VIDEODRIVER");
        int headless = driver && strcmp(driver, "dummy") == 0;
        if (hle_screen_open("Direct3D 7 application", 0, headless) != 0) return 0;
    }
    if(!g_d3d_vt) { static const win32hle_shim m[]={{"IDirect3D7::QueryInterface",d3d_qi},{"IDirect3D7::AddRef",d3d_addref},{"IDirect3D7::Release",d3d_release},{"IDirect3D7::EnumDevices",d3d_enum},{"IDirect3D7::CreateDevice",d3d_create_device},{"IDirect3D7::CreateVertexBuffer",d3d_create_vb},{"IDirect3D7::EnumZBufferFormats",d3d_enum_z},{"IDirect3D7::EvictManagedTextures",d3d_evict},{0,0}}; g_d3d_vt=hle_com_vtable(m); }
    if(!g_d3d)g_d3d=hle_com_new(g_d3d_vt,8);
    return g_d3d;
}
